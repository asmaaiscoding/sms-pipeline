"""
app/scrapers/api_base.py
────────────────────────
Generic API-based scraper base class.

All publisher scrapers (ACM, IEEE, Springer, Wiley, Elsevier, etc.)
share the same two-strategy approach:
  1. OpenAlex API   (primary)  — free, no auth
  2. CrossRef API   (fallback) — free, no auth

Subclasses only need to set class attributes:
  SOURCE_NAME, PUBLISHER, OPENALEX_PUBLISHER, DOI_PREFIX
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

# Shared HTTP session for all API scrapers
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "SMSPipeline/1.0 (academic-review-tool; mailto:noreply@example.com)",
    "Accept": "application/json",
})

_OPENALEX_MAILTO = "mailto=sms-pipeline@example.com"


class APIScraperBase:
    """
    Base class for API-based academic paper collection.

    Subclasses MUST define:
        SOURCE_NAME : str          — display name (e.g. "Wiley Online Library")
        PUBLISHER   : str          — short publisher label
        OPENALEX_PUBLISHER : str   — publisher name as indexed by OpenAlex
        DOI_PREFIX  : str          — CrossRef DOI prefix (e.g. "10.1002")
    """

    SOURCE_NAME: str = ""
    PUBLISHER: str = ""
    OPENALEX_PUBLISHER: str = ""
    DOI_PREFIX: str = ""

    def __init__(self, driver=None, flog: Optional[logging.Logger] = None):
        self.driver = driver  # kept for interface compat — unused
        self.flog = flog or _get_null_logger()
        self.flog.info("[%s] Scraper initialised (API-based)", self.PUBLISHER)

    # ── public entry point ────────────────────────────────────────────────────

    def scrape(
        self,
        search_strings: Dict[str, str],
        year_start: int,
        year_end: int,
    ) -> pd.DataFrame:
        all_papers: List[dict] = []
        tag = self.PUBLISHER
        self.flog.info(
            "[%s] scrape() called — %d search strings, years %d–%d",
            tag, len(search_strings), year_start, year_end,
        )

        for sname, query in search_strings.items():
            for year in range(year_start, year_end + 1):
                self.flog.info("[%s] — %s | year %d —", tag, sname, year)
                logger.info("[%s] %s | year %d", tag, sname, year)

                try:
                    papers = self._collect(query, sname, year)
                except Exception as exc:
                    self.flog.error(
                        "[%s] _collect() EXCEPTION for %s/%d: %s",
                        tag, sname, year, exc, exc_info=True,
                    )
                    papers = []

                for p in papers:
                    p["Search_String"] = sname
                all_papers.extend(papers)

                self.flog.info("[%s] %s | %d: %d papers", tag, sname, year, len(papers))
                random_delay(0.3, 0.8)

        self.flog.info("[%s] scrape() finished — total %d papers", tag, len(all_papers))

        if not all_papers:
            return pd.DataFrame(columns=[
                "Title", "Authors", "Year", "Publisher",
                "DOI", "Link", "Source", "Search_String",
            ])
        return pd.DataFrame(all_papers)

    # ── orchestrator ──────────────────────────────────────────────────────────

    def _collect(self, query: str, sname: str, year: int) -> List[dict]:
        tag = self.PUBLISHER
        papers = self._scrape_via_openalex(query, year)
        if papers:
            self.flog.info("[%s] OpenAlex returned %d papers", tag, len(papers))
            return papers

        self.flog.info("[%s] OpenAlex returned 0 — trying CrossRef", tag)
        papers = self._scrape_via_crossref(query, year)
        if papers:
            self.flog.info("[%s] CrossRef returned %d papers", tag, len(papers))
            return papers

        self.flog.warning("[%s] %s | %d: ALL API approaches returned 0", tag, sname, year)
        return []

    # ══════════════════════════════════════════════════════════════════════════
    #  OPENALEX
    # ══════════════════════════════════════════════════════════════════════════

    def _scrape_via_openalex(self, query: str, year: int) -> List[dict]:
        papers: List[dict] = []
        seen_dois: set = set()
        per_page = 200
        cursor = "*"
        page_num = 0
        max_pages = 50
        tag = self.PUBLISHER

        base_filter = (
            f"publication_year:{year},"
            f"locations.source.publisher:{self.OPENALEX_PUBLISHER}"
        )

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
            self.flog.debug("[%s-OA] GET page %d", tag, page_num)

            try:
                resp = _SESSION.get(url, timeout=30)
            except Exception as exc:
                self.flog.error("[%s-OA] Request failed: %s", tag, exc)
                break

            if resp.status_code != 200:
                if resp.status_code == 429:
                    self.flog.info("[%s-OA] Rate limited — waiting 5s", tag)
                    time.sleep(5)
                    continue
                self.flog.warning("[%s-OA] HTTP %d", tag, resp.status_code)
                break

            data = resp.json()
            results = data.get("results", [])

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

            cursor = data.get("meta", {}).get("next_cursor")
            if not cursor:
                break

            random_delay(0.2, 0.5)

        self.flog.info("[%s-OA] Finished — %d papers", tag, len(papers))
        return papers

    def _openalex_to_paper(self, work: dict, year: int) -> Optional[dict]:
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

    # ══════════════════════════════════════════════════════════════════════════
    #  CROSSREF
    # ══════════════════════════════════════════════════════════════════════════

    def _scrape_via_crossref(self, query: str, year: int) -> List[dict]:
        papers: List[dict] = []
        seen_dois: set = set()
        rows_per_page = 100
        offset = 0
        max_results = 2000
        tag = self.PUBLISHER

        while offset < max_results:
            url = (
                f"https://api.crossref.org/prefixes/{self.DOI_PREFIX}/works"
                f"?query={quote_plus(query)}"
                f"&filter=from-pub-date:{year},until-pub-date:{year}"
                f"&rows={rows_per_page}"
                f"&offset={offset}"
                f"&sort=relevance&order=desc"
            )
            self.flog.debug("[%s-CR] GET offset=%d", tag, offset)

            try:
                resp = _SESSION.get(url, timeout=45)
            except Exception as exc:
                self.flog.error("[%s-CR] Request failed: %s", tag, exc)
                break

            if resp.status_code != 200:
                if resp.status_code == 429:
                    self.flog.info("[%s-CR] Rate limited — waiting 10s", tag)
                    time.sleep(10)
                    continue
                self.flog.warning("[%s-CR] HTTP %d", tag, resp.status_code)
                break

            data = resp.json()
            items = data.get("message", {}).get("items", [])

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

        self.flog.info("[%s-CR] Finished — %d papers", tag, len(papers))
        return papers

    def _crossref_to_paper(self, item: dict, year: int) -> Optional[dict]:
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
