"""
scrapers/ieee.py
----------------
Scraper for IEEE Xplore -- **API-based** (no web scraping).

Uses free academic APIs:
  1. OpenAlex API (primary)
  2. CrossRef API (fallback, DOI prefix 10.1109)

Usage (stand-alone test):
    python -m scrapers.ieee
"""

from __future__ import annotations

import html as html_mod
import time
from typing import Dict, List, Optional
from urllib.parse import quote, quote_plus

import pandas as pd
import requests

from scrapers.base import random_delay

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "SMSPipeline/1.0 (academic-review-tool; mailto:noreply@example.com)",
    "Accept": "application/json",
})

_OPENALEX_MAILTO = "mailto=sms-pipeline@example.com"
_IEEE_DOI_PREFIX = "10.1109"


class IEEEScraper:
    """Collect IEEE Xplore papers via free academic APIs."""

    SOURCE_NAME = "IEEE Xplore"
    PUBLISHER = "IEEE"

    def __init__(self, driver=None):
        self.driver = driver

    def scrape(self, search_strings: Dict[str, str], year_start: int, year_end: int) -> pd.DataFrame:
        all_papers: List[dict] = []
        for sname, query in search_strings.items():
            for year in range(year_start, year_end + 1):
                print(f"\n[IEEE] {sname} | year {year}")
                try:
                    papers = self._collect(query, sname, year)
                except Exception as exc:
                    print(f"  [IEEE] ERROR: {exc}")
                    papers = []
                for p in papers:
                    p["Search_String"] = sname
                all_papers.extend(papers)
                print(f"[IEEE] {sname} | {year}: {len(papers)} papers")
                random_delay(0.3, 0.8)

        if not all_papers:
            return pd.DataFrame(columns=[
                "Title", "Authors", "Year", "Publisher",
                "DOI", "Link", "Source", "Search_String",
            ])
        return pd.DataFrame(all_papers)

    def _collect(self, query: str, sname: str, year: int) -> List[dict]:
        papers = self._scrape_via_openalex(query, year)
        if papers:
            return papers
        print("  OpenAlex returned 0 -- trying CrossRef")
        papers = self._scrape_via_crossref(query, year)
        return papers or []

    def _scrape_via_openalex(self, query: str, year: int) -> List[dict]:
        papers: List[dict] = []
        seen_dois: set = set()
        per_page = 200
        cursor = "*"
        page_num = 0
        max_pages = 50
        base_filter = f"publication_year:{year},locations.source.publisher:IEEE"

        while cursor and page_num < max_pages:
            page_num += 1
            url = (
                f"https://api.openalex.org/works"
                f"?search={quote(query)}"
                f"&filter={quote(base_filter)}"
                f"&per_page={per_page}&cursor={cursor}"
                f"&{_OPENALEX_MAILTO}"
            )
            try:
                resp = _SESSION.get(url, timeout=30)
            except Exception as exc:
                print(f"  [IEEE-OA] Request failed: {exc}")
                break

            if resp.status_code != 200:
                if resp.status_code == 429:
                    time.sleep(5); continue
                break

            data = resp.json()
            results = data.get("results", [])
            meta = data.get("meta", {})
            if not results:
                break

            for work in results:
                paper = self._openalex_to_paper(work, year)
                if paper:
                    dk = paper["DOI"].lower()
                    if dk and dk in seen_dois: continue
                    if dk: seen_dois.add(dk)
                    papers.append(paper)

            print(f"  [IEEE] OpenAlex page {page_num}: +{len(results)} (total {len(papers)})")
            cursor = meta.get("next_cursor")
            if not cursor: break
            random_delay(0.2, 0.5)

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
                f"https://api.crossref.org/prefixes/{_IEEE_DOI_PREFIX}/works"
                f"?query={quote_plus(query)}"
                f"&filter=from-pub-date:{year},until-pub-date:{year}"
                f"&rows={rows_per_page}&offset={offset}"
                f"&sort=relevance&order=desc"
            )
            try:
                resp = _SESSION.get(url, timeout=45)
            except Exception as exc:
                print(f"  [IEEE-CR] Request failed: {exc}")
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

            print(f"  [IEEE] CrossRef offset {offset}: +{len(items)} (total {len(papers)})")
            offset += rows_per_page
            if len(items) < rows_per_page: break
            random_delay(0.5, 1.0)

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


if __name__ == "__main__":
    from config import DETECTION_STRINGS
    scraper = IEEEScraper()
    df = scraper.scrape({"DET-1": DETECTION_STRINGS["DET-1"]}, 2023, 2023)
    print(f"\n--- Results: {len(df)} papers ---")
    print(df[["Title", "Year", "DOI"]].head(10))
