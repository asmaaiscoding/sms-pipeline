"""
scrapers/acm.py
---------------
Scraper for ACM Digital Library.

Multi-strategy approach to bypass Cloudflare:
  1. cloudscraper HTTP session (handles Cloudflare JS challenges)
  2. Plain requests fallback
  3. Selenium fallback

Usage (stand-alone test):
    python -m scrapers.acm
"""

from __future__ import annotations

import html as html_mod
import re
import time
from typing import Dict, List, Optional
from urllib.parse import quote_plus

import pandas as pd
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException

from scrapers.base import (
    setup_driver,
    random_delay,
    ensure_page_ready,
    has_captcha,
    pause_for_human,
    retry,
)
from config import DELAY_BETWEEN_PAGES_MIN, DELAY_BETWEEN_PAGES_MAX

# ── HTTP session ──────────────────────────────────────────────────────────────
try:
    import cloudscraper

    _SESSION = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True},
    )
    print("[ACM] Using cloudscraper for Cloudflare bypass")
except ImportError:
    import requests as _requests

    _SESSION = _requests.Session()
    print("[ACM] cloudscraper not installed — may be blocked by Cloudflare")

_SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
)

# ── Optional BeautifulSoup ────────────────────────────────────────────────────
try:
    from bs4 import BeautifulSoup

    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False


class ACMScraper:
    """Scrape papers from ACM Digital Library year-by-year."""

    SOURCE_NAME = "ACM Digital Library"
    PUBLISHER = "ACM"

    def __init__(self, driver):
        self.driver = driver
        self._first_visit = True
        self._http_consecutive_fails = 0
        self._selenium_blocked = False

    # ── public entry point ────────────────────────────────────────────────────

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

                papers = self._collect(query, sname, year)
                for p in papers:
                    p["Search_String"] = sname
                all_papers.extend(papers)

                print(f"[ACM] {sname} | {year}: {len(papers)} papers")
                random_delay(1, 3)

        if not all_papers:
            return pd.DataFrame(
                columns=[
                    "Title", "Authors", "Year", "Publisher",
                    "DOI", "Link", "Source", "Search_String",
                ]
            )
        return pd.DataFrame(all_papers)

    # ── orchestrator ──────────────────────────────────────────────────────────

    def _collect(self, query: str, sname: str, year: int) -> List[dict]:
        """Try HTTP first, fallback to Selenium."""
        if self._http_consecutive_fails < 5:
            papers = self._scrape_via_http(query, year)
            if papers:
                self._http_consecutive_fails = 0
                return papers
            self._http_consecutive_fails += 1
            print(f"  HTTP returned 0 — trying Selenium")

        if not self._selenium_blocked:
            papers = self._scrape_via_selenium(query, year)
            if papers:
                return papers

        print(f"  [ACM] {sname} | {year}: all approaches failed")
        return []

    # ══════════════════════════════════════════════════════════════════════════
    #  HTTP-BASED SCRAPING (primary)
    # ══════════════════════════════════════════════════════════════════════════

    def _scrape_via_http(self, query: str, year: int) -> List[dict]:
        papers: List[dict] = []
        page_size = 50
        max_pages = 40

        for page_num in range(max_pages):
            try:
                url = (
                    f"https://dl.acm.org/action/doSearch"
                    f"?fillQuickSearch=false&target=advanced"
                    f"&AllField={quote_plus(query)}"
                    f"&AfterYear={year}&BeforeYear={year}"
                    f"&startPage={page_num}&pageSize={page_size}"
                )
                resp = _SESSION.get(url, timeout=30)

                if resp.status_code == 403:
                    print("  [ACM] HTTP 403 — Cloudflare block")
                    return papers

                if resp.status_code != 200:
                    print(f"  [ACM] HTTP {resp.status_code}")
                    return papers

                if self._is_cloudflare_page(resp.text):
                    print("  [ACM] Cloudflare challenge page detected")
                    return papers

                page_papers = self._parse_html(resp.text, year)

                if not page_papers:
                    break

                papers.extend(page_papers)
                print(
                    f"  [ACM] HTTP page {page_num + 1}: "
                    f"+{len(page_papers)} papers (total {len(papers)})"
                )

                if len(page_papers) < page_size:
                    break

                random_delay(2, 4)

            except Exception as exc:
                print(f"  [ACM] HTTP request failed: {exc}")
                break

        return papers

    @staticmethod
    def _is_cloudflare_page(text: str) -> bool:
        lower = text.lower()
        indicators = [
            "just a moment",
            "cf-browser-verification",
            "challenge-platform",
            "turnstile",
            "checking your browser",
        ]
        return any(ind in lower for ind in indicators) and len(text) < 30000

    # ── HTML parsing ──────────────────────────────────────────────────────────

    def _parse_html(self, raw_html: str, year: int) -> List[dict]:
        if _HAS_BS4:
            return self._parse_with_bs4(raw_html, year)
        return self._parse_with_regex(raw_html, year)

    def _parse_with_bs4(self, raw_html: str, year: int) -> List[dict]:
        soup = BeautifulSoup(raw_html, "html.parser")
        papers: List[dict] = []
        seen: set = set()

        # Strategy 1: structured items
        items = soup.select(
            "li.search__item, "
            "div.issue-item, "
            "div.search-result, "
            "div.issue-item-container"
        )
        if items:
            for item in items:
                paper = self._extract_item_bs4(item, year, seen)
                if paper:
                    papers.append(paper)
            if papers:
                return papers

        # Strategy 2: title links
        title_selectors = [
            'h5.issue-item__title a[href*="/doi/"]',
            'span.hlFld-Title a[href*="/doi/10."]',
            'a.issue-item__title[href*="/doi/"]',
            'h5 a[href*="/doi/10."]',
        ]
        for sel in title_selectors:
            for a_tag in soup.select(sel):
                href = a_tag.get("href", "")
                title = html_mod.unescape(a_tag.get_text(strip=True))
                if not title or len(title) < 10 or href in seen:
                    continue
                seen.add(href)
                doi = href.replace("/doi/", "").split("?")[0]
                link = (
                    f"https://dl.acm.org{href}"
                    if not href.startswith("http")
                    else href
                )
                authors = self._find_authors_bs4(a_tag)
                papers.append(
                    {
                        "Title": title,
                        "Authors": authors,
                        "Year": str(year),
                        "Publisher": self.PUBLISHER,
                        "DOI": doi,
                        "Link": link,
                        "Source": self.SOURCE_NAME,
                    }
                )
        return papers

    def _extract_item_bs4(
        self, item, year: int, seen: set
    ) -> Optional[dict]:
        title_el = (
            item.select_one('h5.issue-item__title a[href*="/doi/"]')
            or item.select_one('span.hlFld-Title a[href*="/doi/"]')
            or item.select_one('a[href*="/doi/10."]')
        )
        if not title_el:
            return None

        href = title_el.get("href", "")
        title = html_mod.unescape(title_el.get_text(strip=True))
        if not title or len(title) < 10 or href in seen:
            return None
        seen.add(href)

        doi = href.replace("/doi/", "").split("?")[0] if "/doi/" in href else ""
        link = (
            f"https://dl.acm.org{href}"
            if not href.startswith("http")
            else href
        )

        author_els = item.select(
            'ul[aria-label*="author"] a, '
            "span.hlFld-ContribAuthor a, "
            "li.loa__item a span, "
            "li.loa__item a"
        )
        authors = "; ".join(
            html_mod.unescape(a.get_text(strip=True))
            for a in author_els
            if a.get_text(strip=True) and len(a.get_text(strip=True)) > 2
        )

        yr = str(year)
        year_match = re.search(r"\b(20\d{2})\b", item.get_text())
        if year_match:
            yr = year_match.group(1)

        return {
            "Title": title,
            "Authors": authors,
            "Year": yr,
            "Publisher": self.PUBLISHER,
            "DOI": doi,
            "Link": link,
            "Source": self.SOURCE_NAME,
        }

    @staticmethod
    def _find_authors_bs4(a_tag) -> str:
        parent = a_tag
        for _ in range(7):
            parent = parent.parent
            if parent is None:
                break
            authors = parent.select(
                "span.hlFld-ContribAuthor a, "
                'ul[aria-label*="author"] a, '
                "li.loa__item a span, "
                "li.loa__item a"
            )
            if authors:
                names = [
                    html_mod.unescape(a.get_text(strip=True))
                    for a in authors
                    if a.get_text(strip=True) and len(a.get_text(strip=True)) > 2
                ]
                return "; ".join(names[:20])
        return ""

    # ── Regex fallback ────────────────────────────────────────────────────────

    def _parse_with_regex(self, raw_html: str, year: int) -> List[dict]:
        papers: List[dict] = []
        seen: set = set()

        patterns = [
            re.compile(
                r'<h5[^>]*class="[^"]*issue-item__title[^"]*"[^>]*>\s*'
                r'<a[^>]*href="(/doi/[^"]+)"[^>]*>\s*([^<]+)',
                re.I | re.S,
            ),
            re.compile(
                r"<span[^>]*class=\"[^\"]*hlFld-Title[^\"]*\"[^>]*>.*?"
                r'<a[^>]*href="(/doi/10\.[^"]+)"[^>]*>\s*([^<]+)',
                re.I | re.S,
            ),
            re.compile(
                r'<a[^>]*href="(/doi/10\.[^"]+)"[^>]*class="[^"]*hlFld-Title[^"]*"[^>]*>\s*([^<]+)',
                re.I | re.S,
            ),
        ]
        for pattern in patterns:
            for match in pattern.finditer(raw_html):
                doi_path = match.group(1).strip()
                title = html_mod.unescape(match.group(2).strip())
                if not title or len(title) < 10 or doi_path in seen:
                    continue
                seen.add(doi_path)
                doi = doi_path.replace("/doi/", "").split("?")[0]
                link = f"https://dl.acm.org{doi_path}"
                authors = self._find_authors_regex(raw_html, doi_path)
                papers.append(
                    {
                        "Title": title,
                        "Authors": authors,
                        "Year": str(year),
                        "Publisher": self.PUBLISHER,
                        "DOI": doi,
                        "Link": link,
                        "Source": self.SOURCE_NAME,
                    }
                )
        return papers

    @staticmethod
    def _find_authors_regex(raw_html: str, doi_path: str) -> str:
        idx = raw_html.find(doi_path)
        if idx < 0:
            return ""
        window = raw_html[idx : idx + 3000]
        pat = re.compile(
            r"<span[^>]*class=\"[^\"]*hlFld-ContribAuthor[^\"]*\"[^>]*>\s*"
            r"<a[^>]*>\s*([^<]+)",
            re.I | re.S,
        )
        authors = [
            html_mod.unescape(m.group(1).strip()) for m in pat.finditer(window)
        ]
        return "; ".join(authors[:20])

    # ══════════════════════════════════════════════════════════════════════════
    #  SELENIUM FALLBACK
    # ══════════════════════════════════════════════════════════════════════════

    def _scrape_via_selenium(self, query: str, year: int) -> List[dict]:
        encoded = quote_plus(query)
        url = (
            f"https://dl.acm.org/action/doSearch"
            f"?fillQuickSearch=false&target=advanced"
            f"&AllField={encoded}"
            f"&AfterYear={year}&BeforeYear={year}"
        )

        self.driver.get(url)
        random_delay(4, 7)
        ensure_page_ready(self.driver, self._first_visit)
        self._first_visit = False

        if has_captcha(self.driver):
            pause_for_human("CAPTCHA detected on ACM — solve it, then press ENTER.")

        return self._selenium_scrape_pages(year)

    def _selenium_scrape_pages(self, year: int) -> List[dict]:
        papers: List[dict] = []
        seen: set = set()
        page = 1
        stale = 0

        while True:
            print(f"  Page {page} …")
            random_delay(2, 4)

            links = self.driver.find_elements(
                By.XPATH, "//a[contains(@href, '/doi/10.')]"
            )

            if not links:
                if has_captcha(self.driver):
                    pause_for_human("CAPTCHA on ACM — solve it, then press ENTER.")
                    stale += 1
                    if stale >= 3:
                        break
                    continue
                break

            new_count = 0
            for el in links:
                try:
                    href = el.get_attribute("href")
                    if not href or href in seen:
                        continue
                    title = el.text.strip()
                    if not title or len(title) < 10:
                        continue
                    seen.add(href)
                    doi = href.split("/doi/")[-1].split("?")[0] if "/doi/" in href else ""
                    authors = self._selenium_extract_authors(el)
                    papers.append(
                        {
                            "Title": title,
                            "Authors": authors,
                            "Year": str(year),
                            "Publisher": self.PUBLISHER,
                            "DOI": doi,
                            "Link": href,
                            "Source": self.SOURCE_NAME,
                        }
                    )
                    new_count += 1
                except Exception:
                    continue

            print(f"  Found {new_count} new papers (total {len(papers)})")

            if new_count == 0:
                stale += 1
                if stale >= 3:
                    break
            else:
                stale = 0

            if not self._selenium_next_page():
                break
            page += 1

        return papers

    @staticmethod
    def _selenium_extract_authors(link_el) -> str:
        try:
            parent = link_el
            for _ in range(5):
                parent = parent.find_element(By.XPATH, "..")
                try:
                    els = parent.find_elements(
                        By.XPATH,
                        ".//*[contains(@class,'author')]//a | "
                        ".//ul[contains(@aria-label,'author')]//a | "
                        ".//li[contains(@class,'loa')]//a",
                    )
                    names = [
                        a.text.strip()
                        for a in els
                        if a.text.strip() and len(a.text.strip()) > 2
                    ]
                    if names:
                        return "; ".join(names[:20])
                except Exception:
                    pass
        except Exception:
            pass
        return ""

    def _selenium_next_page(self) -> bool:
        try:
            btn = self.driver.find_element(
                By.XPATH,
                "//a[@aria-label='Next page'] | "
                "//a[contains(@class,'pagination__btn--next')] | "
                "//button[contains(@class,'pagination__btn--next')] | "
                "//li[@class='pagination__item--next']//a",
            )
            classes = (btn.get_attribute("class") or "").lower()
            if "disabled" in classes or btn.get_attribute("aria-disabled") == "true":
                return False
            self.driver.execute_script("arguments[0].scrollIntoView();", btn)
            time.sleep(1)
            self.driver.execute_script("arguments[0].click();", btn)
            random_delay(DELAY_BETWEEN_PAGES_MIN, DELAY_BETWEEN_PAGES_MAX)
            return True
        except NoSuchElementException:
            return False


# ── stand-alone test ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    from config import DETECTION_STRINGS

    drv = setup_driver()
    try:
        scraper = ACMScraper(drv)
        df = scraper.scrape({"DET-1": DETECTION_STRINGS["DET-1"]}, 2023, 2023)
        print(df)
    finally:
        drv.quit()
