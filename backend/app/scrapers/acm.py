"""
app/scrapers/acm.py
-------------------
Scraper for ACM Digital Library -- **API-based** (no web scraping).

Uses free academic APIs that index ACM papers, completely avoiding
Cloudflare / CAPTCHA issues:

  1. OpenAlex API   (primary)  -- excellent coverage, no auth required
  2. CrossRef API   (fallback) -- indexes every DOI minted by ACM

The Selenium driver parameter is accepted for interface compatibility
with the other scrapers but is NOT used.
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

# -- Shared HTTP session ---
_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": "SMSPipeline/1.0 (academic-review-tool; mailto:noreply@example.com)",
        "Accept": "application/json",
    }
)

# OpenAlex polite pool -- add an email to get faster rate limits
_OPENALEX_MAILTO = "mailto=sms-pipeline@example.com"

# ACMs CrossRef DOI prefix
_ACM_DOI_PREFIX = "10.1145"


class ACMScraper:
    """Collect ACM Digital Library papers via free academic APIs."""

    SOURCE_NAME = "ACM Digital Library"
    PUBLISHER = "ACM"

    def __init__(self, driver=None, flog: Optional[logging.Logger] = None):
        # driver kept for interface compat -- unused
        self.driver = driver
        self.flog = flog or _get_null_logger()
        self.flog.info("[ACM] Scraper initialised (API-based -- no Selenium/CAPTCHA)")

    # -- public entry point ---

    def scrape(
        self,
        search_strings: Dict[str, str],
        year_start: int,
        year_end: int,
    ) -> pd.DataFrame:
        all_papers: List[dict] = []
        self.flog.info(
            "[ACM] scrape() called -- %d search strings, years %d-%d",
            len(search_strings), year_start, year_end,
        )

        for sname, query in search_strings.items():
            for year in range(year_start, year_end + 1):
                self.flog.info("[ACM] -- %s | year %d --", sname, year)
                logger.info("[ACM] %s | year %d", sname, year)

                try:
                    papers = self._collect(query, sname, year)
                except Exception as exc:
                    self.flog.error(
                        "[ACM] _collect() EXCEPTION for %s/%d: %s",
                        sname, year, exc, exc_info=True,
                    )
                    papers = []

                for p in papers:
                    p["Search_String"] = sname
                all_papers.extend(papers)

                self.flog.info(
                    "[ACM] %s | %d: %d papers collected", sname, year, len(papers)
                )
                logger.info("[ACM] %s | %d: %d papers", sname, year, len(papers))
                random_delay(0.3, 0.8)

        self.flog.info(
            "[ACM] scrape() finished -- total %d papers across all strings/years",
            len(all_papers),
        )

        if not all_papers:
            return pd.DataFrame(
                columns=[
                    "Title", "Authors", "Year", "Publisher",
                    "DOI", "Link", "Source", "Search_String",
                ]
            )
        return pd.DataFrame(all_papers)

    # -- orchestrator ---

    def _collect(self, query: str, sname: str, year: int) -> List[dict]:
        """Try OpenAlex first, fall back to CrossRef."""
        self.flog.debug("[ACM] _collect(%s, %d) -- trying OpenAlex first", sname, year)

        papers = self._scrape_via_openalex(query, year)
        if papers:
            self.flog.info("[ACM] OpenAlex returned %d papers", len(papers))
            return papers

        self.flog.info("[ACM] OpenAlex returned 0 -- trying CrossRef fallback")
        papers = self._scrape_via_crossref(query, year)
        if papers:
            self.flog.info("[ACM] CrossRef returned %d papers", len(papers))
            return papers

        self.flog.warning("[ACM] %s | %d: ALL API approaches returned 0", sname, year)
        return []

    # ==================================================================
    #  OPENALEX API  (primary)
    # ==================================================================

    def _scrape_via_openalex(self, query: str, year: int) -> List[dict]:
        """
        Query OpenAlex for ACM-published works matching the search string.

        OpenAlex docs: https://docs.openalex.org/api-entities/works
        """
        papers: List[dict] = []
        seen_dois: set = set()
        per_page = 200
        cursor = "*"
        page_num = 0
        max_pages = 50

        base_filter = f"publication_year:{year},locations.source.publisher:ACM"

        while cursor and page_num < max_pages:
            page_num += 1
            url = (
                f"https://api.openalex.org/works"
                f"?search={quote(query)}"
                f"&filter={quote(base_filter)}"
                f"&per_page={per_page}"
                f"&cursor={cursor}"
                f"&{_OPENALEX_MAILTO}"
            )
            self.flog.debug("[ACM-OA] GET page %d: %s", page_num, url)

            try:
                resp = _SESSION.get(url, timeout=30)
            except Exception as exc:
                self.flog.error("[ACM-OA] Request failed: %s", exc)
                break

            if resp.status_code != 200:
                self.flog.warning(
                    "[ACM-OA] HTTP %d -- body: %s",
                    resp.status_code, resp.text[:500],
                )
                if resp.status_code == 429:
                    self.flog.info("[ACM-OA] Rate limited -- waiting 5s")
                    time.sleep(5)
                    continue
                break

            data = resp.json()
            results = data.get("results", [])
            meta = data.get("meta", {})

            self.flog.debug(
                "[ACM-OA] Page %d: %d results (total_count=%s)",
                page_num, len(results), meta.get("count", "?"),
            )

            if not results:
                break

            for work in results:
                paper = self._openalex_to_paper(work, year)
                if paper:
                    doi_key = paper["DOI"].lower()
                    if doi_key and doi_key in seen_dois:
                        continue
                    if doi_key:
                        seen_dois.add(doi_key)
                    papers.append(paper)

            cursor = meta.get("next_cursor")
            if not cursor:
                break

            random_delay(0.2, 0.5)

        self.flog.info("[ACM-OA] Finished -- %d papers total", len(papers))
        return papers

    def _openalex_to_paper(self, work: dict, year: int) -> Optional[dict]:
        """Convert an OpenAlex work object to our paper dict."""
        title = (work.get("title") or "").strip()
        if not title or len(title) < 5:
            return None

        doi_url = work.get("doi") or ""
        doi = doi_url.replace("https://doi.org/", "").replace("http://doi.org/", "")

        link = doi_url or work.get("id", "")
        if doi and not link.startswith("http"):
            link = f"https://doi.org/{doi}"

        authorships = work.get("authorships", [])
        authors = "; ".join(
            a.get("author", {}).get("display_name", "")
            for a in authorships
            if a.get("author", {}).get("display_name")
        )

        yr = work.get("publication_year") or year

        return {
            "Title": html_mod.unescape(title),
            "Authors": authors,
            "Year": str(yr),
            "Publisher": self.PUBLISHER,
            "DOI": doi,
            "Link": link,
            "Source": self.SOURCE_NAME,
        }

    # ==================================================================
    #  CROSSREF API  (fallback)
    # ==================================================================

    def _scrape_via_crossref(self, query: str, year: int) -> List[dict]:
        """
        Query CrossRef for ACM DOIs matching the search string.
        Filters by ACM DOI prefix 10.1145 and publication year.
        """
        papers: List[dict] = []
        seen_dois: set = set()
        rows_per_page = 100
        offset = 0
        max_results = 2000

        while offset < max_results:
            url = (
                f"https://api.crossref.org/prefixes/{_ACM_DOI_PREFIX}/works"
                f"?query={quote_plus(query)}"
                f"&filter=from-pub-date:{year},until-pub-date:{year}"
                f"&rows={rows_per_page}"
                f"&offset={offset}"
                f"&sort=relevance"
                f"&order=desc"
            )
            self.flog.debug("[ACM-CR] GET offset=%d: %s", offset, url)

            try:
                resp = _SESSION.get(url, timeout=45)
            except Exception as exc:
                self.flog.error("[ACM-CR] Request failed: %s", exc)
                break

            if resp.status_code != 200:
                self.flog.warning("[ACM-CR] HTTP %d", resp.status_code)
                if resp.status_code == 429:
                    self.flog.info("[ACM-CR] Rate limited -- waiting 10s")
                    time.sleep(10)
                    continue
                break

            data = resp.json()
            items = data.get("message", {}).get("items", [])

            self.flog.debug(
                "[ACM-CR] Got %d items (total-results=%s)",
                len(items),
                data.get("message", {}).get("total-results", "?"),
            )

            if not items:
                break

            for item in items:
                paper = self._crossref_to_paper(item, year)
                if paper:
                    doi_key = paper["DOI"].lower()
                    if doi_key and doi_key in seen_dois:
                        continue
                    if doi_key:
                        seen_dois.add(doi_key)
                    papers.append(paper)

            offset += rows_per_page

            if len(items) < rows_per_page:
                break

            random_delay(0.5, 1.0)

        self.flog.info("[ACM-CR] Finished -- %d papers total", len(papers))
        return papers

    def _crossref_to_paper(self, item: dict, year: int) -> Optional[dict]:
        """Convert a CrossRef work item to our paper dict."""
        titles = item.get("title", [])
        title = titles[0].strip() if titles else ""
        if not title or len(title) < 5:
            return None

        doi = item.get("DOI", "")
        link = f"https://doi.org/{doi}" if doi else item.get("URL", "")

        author_list = item.get("author", [])
        authors = "; ".join(
            f"{a.get('given', '')} {a.get('family', '')}".strip()
            for a in author_list
            if a.get("family")
        )

        yr = year
        for date_key in ("published-print", "published-online", "created"):
            date_info = item.get(date_key)
            if date_info:
                parts = date_info.get("date-parts", [[]])[0]
                if parts and parts[0]:
                    yr = parts[0]
                    break

        return {
            "Title": html_mod.unescape(title),
            "Authors": authors,
            "Year": str(yr),
            "Publisher": self.PUBLISHER,
            "DOI": doi,
            "Link": link,
            "Source": self.SOURCE_NAME,
        }
