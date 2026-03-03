"""
app/scrapers/springer.py
------------------------
Scraper for Springer -- **API-based** (no web scraping).

Uses free academic APIs:
  1. OpenAlex API   (primary)
  2. CrossRef API   (fallback, DOI prefix 10.1007)

The Selenium driver parameter is accepted for interface compat but is NOT used.
"""

from __future__ import annotations

import html as html_mod
import logging
import time
from typing import Dict, List, Optional
from urllib.parse import quote, quote_plus

import pandas as pd
import requests

from app.scrapers.base import random_delay, _get_null_logger

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "SMSPipeline/1.0 (academic-review-tool; mailto:noreply@example.com)",
    "Accept": "application/json",
})

_OPENALEX_MAILTO = "mailto=sms-pipeline@example.com"
_SPRINGER_DOI_PREFIX = "10.1007"


class SpringerScraper:
    """Collect Springer papers via free academic APIs."""

    SOURCE_NAME = "Springer Link"
    PUBLISHER = "Springer"

    def __init__(self, driver=None, flog: Optional[logging.Logger] = None):
        self.driver = driver
        self.flog = flog or _get_null_logger()
        self.flog.info("[Springer] Scraper initialised (API-based)")

    def scrape(self, search_strings: Dict[str, str], year_start: int, year_end: int) -> pd.DataFrame:
        all_papers: List[dict] = []
        self.flog.info("[Springer] scrape() called -- %d search strings, years %d-%d",
                       len(search_strings), year_start, year_end)

        for sname, query in search_strings.items():
            for year in range(year_start, year_end + 1):
                self.flog.info("[Springer] -- %s | year %d --", sname, year)
                logger.info("[Springer] %s | year %d", sname, year)
                try:
                    papers = self._collect(query, sname, year)
                except Exception as exc:
                    self.flog.error("[Springer] _collect() EXCEPTION for %s/%d: %s", sname, year, exc, exc_info=True)
                    papers = []
                for p in papers:
                    p["Search_String"] = sname
                all_papers.extend(papers)
                self.flog.info("[Springer] %s | %d: %d papers collected", sname, year, len(papers))
                random_delay(0.3, 0.8)

        self.flog.info("[Springer] scrape() finished -- total %d papers", len(all_papers))

        if not all_papers:
            return pd.DataFrame(columns=[
                "Title", "Authors", "Year", "Publisher",
                "DOI", "Link", "Source", "Search_String",
            ])
        return pd.DataFrame(all_papers)

    def _collect(self, query: str, sname: str, year: int) -> List[dict]:
        papers = self._scrape_via_openalex(query, year)
        if papers:
            self.flog.info("[Springer] OpenAlex returned %d papers", len(papers))
            return papers
        self.flog.info("[Springer] OpenAlex returned 0 -- trying CrossRef")
        papers = self._scrape_via_crossref(query, year)
        if papers:
            self.flog.info("[Springer] CrossRef returned %d papers", len(papers))
            return papers
        self.flog.warning("[Springer] %s | %d: ALL API approaches returned 0", sname, year)
        return []

    def _scrape_via_openalex(self, query: str, year: int) -> List[dict]:
        papers: List[dict] = []
        seen_dois: set = set()
        per_page = 200
        cursor = "*"
        page_num = 0
        max_pages = 50
        base_filter = f"publication_year:{year},locations.source.publisher:Springer"

        while cursor and page_num < max_pages:
            page_num += 1
            url = (
                f"https://api.openalex.org/works"
                f"?search={quote(query)}"
                f"&filter={quote(base_filter)}"
                f"&per_page={per_page}&cursor={cursor}"
                f"&{_OPENALEX_MAILTO}"
            )
            self.flog.debug("[Springer-OA] GET page %d", page_num)
            try:
                resp = _SESSION.get(url, timeout=30)
            except Exception as exc:
                self.flog.error("[Springer-OA] Request failed: %s", exc)
                break
            if resp.status_code != 200:
                if resp.status_code == 429: time.sleep(5); continue
                break
            data = resp.json()
            results = data.get("results", [])
            meta = data.get("meta", {})
            if not results: break
            for work in results:
                paper = self._openalex_to_paper(work, year)
                if paper:
                    dk = paper["DOI"].lower()
                    if dk and dk in seen_dois: continue
                    if dk: seen_dois.add(dk)
                    papers.append(paper)
            cursor = meta.get("next_cursor")
            if not cursor: break
            random_delay(0.2, 0.5)

        self.flog.info("[Springer-OA] Finished -- %d papers", len(papers))
        return papers

    def _openalex_to_paper(self, work: dict, year: int) -> Optional[dict]:
        title = (work.get("title") or "").strip()
        if not title or len(title) < 5: return None
        doi_url = work.get("doi") or ""
        doi = doi_url.replace("https://doi.org/", "").replace("http://doi.org/", "")
        link = doi_url or work.get("id", "")
        if doi and not link.startswith("http"): link = f"https://doi.org/{doi}"
        authorships = work.get("authorships", [])
        authors = "; ".join(a.get("author", {}).get("display_name", "") for a in authorships if a.get("author", {}).get("display_name"))
        yr = work.get("publication_year") or year
        return {"Title": html_mod.unescape(title), "Authors": authors, "Year": str(yr), "Publisher": self.PUBLISHER, "DOI": doi, "Link": link, "Source": self.SOURCE_NAME}

    def _scrape_via_crossref(self, query: str, year: int) -> List[dict]:
        papers: List[dict] = []
        seen_dois: set = set()
        rows_per_page = 100
        offset = 0
        max_results = 2000

        while offset < max_results:
            url = (
                f"https://api.crossref.org/prefixes/{_SPRINGER_DOI_PREFIX}/works"
                f"?query={quote_plus(query)}"
                f"&filter=from-pub-date:{year},until-pub-date:{year}"
                f"&rows={rows_per_page}&offset={offset}"
                f"&sort=relevance&order=desc"
            )
            self.flog.debug("[Springer-CR] GET offset=%d", offset)
            try:
                resp = _SESSION.get(url, timeout=45)
            except Exception as exc:
                self.flog.error("[Springer-CR] Request failed: %s", exc)
                break
            if resp.status_code != 200:
                if resp.status_code == 429: time.sleep(10); continue
                break
            items = resp.json().get("message", {}).get("items", [])
            if not items: break
            for item in items:
                paper = self._crossref_to_paper(item, year)
                if paper:
                    dk = paper["DOI"].lower()
                    if dk and dk in seen_dois: continue
                    if dk: seen_dois.add(dk)
                    papers.append(paper)
            offset += rows_per_page
            if len(items) < rows_per_page: break
            random_delay(0.5, 1.0)

        self.flog.info("[Springer-CR] Finished -- %d papers", len(papers))
        return papers

    def _crossref_to_paper(self, item: dict, year: int) -> Optional[dict]:
        titles = item.get("title", [])
        title = titles[0].strip() if titles else ""
        if not title or len(title) < 5: return None
        doi = item.get("DOI", "")
        link = f"https://doi.org/{doi}" if doi else item.get("URL", "")
        author_list = item.get("author", [])
        authors = "; ".join(f"{a.get('given', '')} {a.get('family', '')}".strip() for a in author_list if a.get("family"))
        yr = year
        for dk in ("published-print", "published-online", "created"):
            di = item.get(dk)
            if di:
                parts = di.get("date-parts", [[]])[0]
                if parts and parts[0]: yr = parts[0]; break
        return {"Title": html_mod.unescape(title), "Authors": authors, "Year": str(yr), "Publisher": self.PUBLISHER, "DOI": doi, "Link": link, "Source": self.SOURCE_NAME}
