"""
scrapers/springer.py
--------------------
Scraper for Springer -- **API-based** (no web scraping).

Uses free academic APIs:
  1. OpenAlex API (primary)
  2. CrossRef API (fallback, DOI prefix 10.1007)

Usage (stand-alone test):
    python -m scrapers.springer
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
_SPRINGER_DOI_PREFIX = "10.1007"


class SpringerScraper:
    SOURCE_NAME = "Springer Link"
    PUBLISHER = "Springer"

    def __init__(self, driver=None):
        self.driver = driver

    def scrape(self, search_strings: Dict[str, str], year_start: int, year_end: int) -> pd.DataFrame:
        all_papers: List[dict] = []
        for sname, query in search_strings.items():
            for year in range(year_start, year_end + 1):
                print(f"\n[Springer] {sname} | year {year}")
                try:
                    papers = self._collect(query, sname, year)
                except Exception as exc:
                    print(f"  [Springer] ERROR: {exc}")
                    papers = []
                for p in papers:
                    p["Search_String"] = sname
                all_papers.extend(papers)
                print(f"[Springer] {sname} | {year}: {len(papers)} papers")
                random_delay(0.3, 0.8)
        if not all_papers:
            return pd.DataFrame(columns=["Title", "Authors", "Year", "Publisher", "DOI", "Link", "Source", "Search_String"])
        return pd.DataFrame(all_papers)

    def _collect(self, query: str, sname: str, year: int) -> List[dict]:
        papers = self._scrape_via_openalex(query, year)
        if papers: return papers
        print("  OpenAlex returned 0 -- trying CrossRef")
        return self._scrape_via_crossref(query, year) or []

    def _scrape_via_openalex(self, query: str, year: int) -> List[dict]:
        papers, seen, cursor, page_num = [], set(), "*", 0
        base_filter = f"publication_year:{year},locations.source.publisher:Springer"
        while cursor and page_num < 50:
            page_num += 1
            url = f"https://api.openalex.org/works?search={quote(query)}&filter={quote(base_filter)}&per_page=200&cursor={cursor}&{_OPENALEX_MAILTO}"
            try: resp = _SESSION.get(url, timeout=30)
            except: break
            if resp.status_code != 200:
                if resp.status_code == 429: time.sleep(5); continue
                break
            data = resp.json()
            results = data.get("results", [])
            if not results: break
            for w in results:
                p = self._openalex_to_paper(w, year)
                if p:
                    dk = p["DOI"].lower()
                    if dk and dk in seen: continue
                    if dk: seen.add(dk)
                    papers.append(p)
            print(f"  [Springer] OpenAlex page {page_num}: +{len(results)} (total {len(papers)})")
            cursor = data.get("meta", {}).get("next_cursor")
            if not cursor: break
            random_delay(0.2, 0.5)
        return papers

    def _openalex_to_paper(self, work, year):
        title = (work.get("title") or "").strip()
        if not title or len(title) < 5: return None
        doi_url = work.get("doi") or ""
        doi = doi_url.replace("https://doi.org/", "").replace("http://doi.org/", "")
        link = doi_url or work.get("id", "")
        if doi and not link.startswith("http"): link = f"https://doi.org/{doi}"
        authors = "; ".join(a.get("author",{}).get("display_name","") for a in work.get("authorships",[]) if a.get("author",{}).get("display_name"))
        return {"Title": html_mod.unescape(title), "Authors": authors, "Year": str(work.get("publication_year") or year), "Publisher": self.PUBLISHER, "DOI": doi, "Link": link, "Source": self.SOURCE_NAME}

    def _scrape_via_crossref(self, query: str, year: int) -> List[dict]:
        papers, seen, offset = [], set(), 0
        while offset < 2000:
            url = f"https://api.crossref.org/prefixes/{_SPRINGER_DOI_PREFIX}/works?query={quote_plus(query)}&filter=from-pub-date:{year},until-pub-date:{year}&rows=100&offset={offset}&sort=relevance&order=desc"
            try: resp = _SESSION.get(url, timeout=45)
            except: break
            if resp.status_code != 200:
                if resp.status_code == 429: time.sleep(10); continue
                break
            items = resp.json().get("message", {}).get("items", [])
            if not items: break
            for item in items:
                p = self._crossref_to_paper(item, year)
                if p:
                    dk = p["DOI"].lower()
                    if dk and dk in seen: continue
                    if dk: seen.add(dk)
                    papers.append(p)
            print(f"  [Springer] CrossRef offset {offset}: +{len(items)} (total {len(papers)})")
            offset += 100
            if len(items) < 100: break
            random_delay(0.5, 1.0)
        return papers

    def _crossref_to_paper(self, item, year):
        titles = item.get("title", [])
        title = titles[0].strip() if titles else ""
        if not title or len(title) < 5: return None
        doi = item.get("DOI", "")
        link = f"https://doi.org/{doi}" if doi else item.get("URL", "")
        authors = "; ".join(f"{a.get('given','')} {a.get('family','')}".strip() for a in item.get("author",[]) if a.get("family"))
        yr = year
        for dk in ("published-print", "published-online", "created"):
            di = item.get(dk)
            if di:
                parts = di.get("date-parts", [[]])[0]
                if parts and parts[0]: yr = parts[0]; break
        return {"Title": html_mod.unescape(title), "Authors": authors, "Year": str(yr), "Publisher": self.PUBLISHER, "DOI": doi, "Link": link, "Source": self.SOURCE_NAME}


if __name__ == "__main__":
    from config import DETECTION_STRINGS
    scraper = SpringerScraper()
    df = scraper.scrape({"DET-1": DETECTION_STRINGS["DET-1"]}, 2023, 2023)
    print(f"\n--- Results: {len(df)} papers ---")
    print(df[["Title", "Year", "DOI"]].head(10))
