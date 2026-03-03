"""
scrapers/acm.py
---------------
Scraper for ACM Digital Library -- **API-based** (no web scraping).

Uses free academic APIs that index ACM papers, completely avoiding
Cloudflare / CAPTCHA issues:

  1. OpenAlex API   (primary)  -- excellent coverage, no auth required
  2. CrossRef API   (fallback) -- indexes every DOI minted by ACM

The Selenium driver parameter is accepted for interface compatibility
with the other scrapers but is NOT used.

Usage (stand-alone test):
    python -m scrapers.acm
"""

from __future__ import annotations

import html as html_mod
import time
from typing import Dict, List, Optional
from urllib.parse import quote, quote_plus

import pandas as pd
import requests

from scrapers.base import random_delay

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

    def __init__(self, driver=None):
        # driver kept for interface compat -- unused
        self.driver = driver

    # -- public entry point ---

    def scrape(
        self,
        search_strings: Dict[str, str],
        year_start: int,
        year_end: int,
    ) -> pd.DataFrame:
        all_papers: List[dict] = []

        for sname, query in search_strings.items():
            for year in range(year_start, year_end + 1):
                print(f"\n[ACM] {sname} | year {year}")

                try:
                    papers = self._collect(query, sname, year)
                except Exception as exc:
                    print(f"  [ACM] ERROR: {exc}")
                    papers = []

                for p in papers:
                    p["Search_String"] = sname
                all_papers.extend(papers)

                print(f"[ACM] {sname} | {year}: {len(papers)} papers")
                random_delay(0.3, 0.8)

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
        papers = self._scrape_via_openalex(query, year)
        if papers:
            return papers

        print("  OpenAlex returned 0 -- trying CrossRef fallback")
        papers = self._scrape_via_crossref(query, year)
        if papers:
            return papers

        print(f"  [ACM] {sname} | {year}: all approaches failed")
        return []

    # ==================================================================
    #  OPENALEX API  (primary)
    # ==================================================================

    def _scrape_via_openalex(self, query: str, year: int) -> List[dict]:
        """
        Query OpenAlex for ACM-published works matching the search string.
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

            try:
                resp = _SESSION.get(url, timeout=30)
            except Exception as exc:
                print(f"  [ACM-OA] Request failed: {exc}")
                break

            if resp.status_code != 200:
                print(f"  [ACM-OA] HTTP {resp.status_code}")
                if resp.status_code == 429:
                    print("  [ACM-OA] Rate limited -- waiting 5s")
                    time.sleep(5)
                    continue
                break

            data = resp.json()
            results = data.get("results", [])
            meta = data.get("meta", {})

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

            print(
                f"  [ACM] OpenAlex page {page_num}: "
                f"+{len(results)} results (total collected {len(papers)})"
            )

            cursor = meta.get("next_cursor")
            if not cursor:
                break

            random_delay(0.2, 0.5)

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

            try:
                resp = _SESSION.get(url, timeout=45)
            except Exception as exc:
                print(f"  [ACM-CR] Request failed: {exc}")
                break

            if resp.status_code != 200:
                print(f"  [ACM-CR] HTTP {resp.status_code}")
                if resp.status_code == 429:
                    print("  [ACM-CR] Rate limited -- waiting 10s")
                    time.sleep(10)
                    continue
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

            print(
                f"  [ACM] CrossRef offset {offset}: "
                f"+{len(items)} items (total collected {len(papers)})"
            )

            offset += rows_per_page

            if len(items) < rows_per_page:
                break

            random_delay(0.5, 1.0)

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


# -- stand-alone test ---
if __name__ == "__main__":
    from config import DETECTION_STRINGS

    scraper = ACMScraper()
    df = scraper.scrape({"DET-1": DETECTION_STRINGS["DET-1"]}, 2023, 2023)
    print(f"\n--- Results: {len(df)} papers ---")
    print(df[["Title", "Year", "DOI"]].head(10))
