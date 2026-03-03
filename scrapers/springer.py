"""
scrapers/springer.py
--------------------
Scraper for Springer Link.

Primary: HTTP requests + HTML parsing (Springer does not use Cloudflare).
Fallback: Selenium.
Adaptive month-subdivision when a year returns > 1000 results.

Usage (stand-alone test):
    python -m scrapers.springer
"""

from __future__ import annotations

import html as html_mod
import re
import time
from typing import Dict, List, Optional
from urllib.parse import quote_plus

import pandas as pd
import requests
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException

from scrapers.base import (
    setup_driver,
    random_delay,
    ensure_page_ready,
    has_captcha,
    pause_for_human,
)
from config import DELAY_BETWEEN_PAGES_MIN, DELAY_BETWEEN_PAGES_MAX

MAX_RESULTS_THRESHOLD = 1000

# ── HTTP session ──────────────────────────────────────────────────────────────
_SESSION = requests.Session()
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
    }
)

# ── Optional BeautifulSoup ────────────────────────────────────────────────────
try:
    from bs4 import BeautifulSoup

    _HAS_BS4 = True
except ImportError:
    _HAS_BS4 = False


class SpringerScraper:
    """Scrape papers from Springer Link year-by-year with adaptive subdivision."""

    SOURCE_NAME = "Springer Link"
    PUBLISHER = "Springer"

    def __init__(self, driver):
        self.driver = driver
        self._first_visit = True
        self._http_works = True

    # ── public entry point ────────────────────────────────────────────────────

    def scrape(
        self,
        search_strings: Dict[str, str],
        year_start: int,
        year_end: int,
    ) -> pd.DataFrame:
        all_papers: List[dict] = []

        for year in range(year_start, year_end + 1):
            year_papers = self._scrape_year(year, search_strings)
            all_papers.extend(year_papers)
            random_delay(2, 4)

        if not all_papers:
            return pd.DataFrame(
                columns=[
                    "Title", "Authors", "Year", "Publisher",
                    "DOI", "Link", "Source", "Search_String",
                ]
            )
        return pd.DataFrame(all_papers)

    def _scrape_year(
        self, year: int, search_strings: Dict[str, str]
    ) -> List[dict]:
        print(f"\n{'=' * 60}")
        print(f"[Springer] YEAR {year}")
        print(f"{'=' * 60}")
        year_papers: List[dict] = []

        for sname, query in search_strings.items():
            print(f"\n[Springer] {sname} | year {year}")
            papers = self._scrape_adaptive(query, sname, year)
            for p in papers:
                p["Search_String"] = sname
            year_papers.extend(papers)
            print(f"[Springer] {sname} | {year}: {len(papers)} papers")
            time.sleep(3)

        return year_papers

    # ── adaptive subdivision ──────────────────────────────────────────────────

    def _scrape_adaptive(
        self,
        query: str,
        sname: str,
        year: int,
        start_month: Optional[int] = None,
        end_month: Optional[int] = None,
    ) -> List[dict]:
        if start_month is None:
            start_month, end_month = 1, 12

        total = self._get_total(query, year, start_month, end_month)

        if total > MAX_RESULTS_THRESHOLD and start_month != end_month:
            print(f"  {total:,} results — subdividing")
            papers: List[dict] = []
            if end_month - start_month + 1 == 12:
                for m in range(1, 13):
                    papers.extend(
                        self._scrape_adaptive(query, sname, year, m, m)
                    )
                    time.sleep(2)
            else:
                mid = start_month + (end_month - start_month) // 2
                papers.extend(
                    self._scrape_adaptive(query, sname, year, start_month, mid)
                )
                time.sleep(2)
                papers.extend(
                    self._scrape_adaptive(
                        query, sname, year, mid + 1, end_month
                    )
                )
            return papers

        if total > MAX_RESULTS_THRESHOLD:
            print(
                f"  Warning: {total:,} results in single month — some may be missed"
            )

        if total == 0:
            return []

        # Scrape all pages
        if self._http_works:
            papers = self._scrape_all_pages_http(
                query, year, start_month, end_month
            )
            if papers:
                return papers

        # Fallback to Selenium
        self._navigate_selenium(query, year, start_month, end_month)
        return self._scrape_all_pages_selenium()

    # ── URL builder ───────────────────────────────────────────────────────────

    @staticmethod
    def _build_url(
        query: str,
        year: int,
        start_month: Optional[int] = None,
        end_month: Optional[int] = None,
        page: int = 1,
    ) -> str:
        encoded = quote_plus(query)
        url = (
            f"https://link.springer.com/search"
            f"?query={encoded}"
            f"&facet-content-type=Article"
            f"&date-facet-mode=between"
            f"&facet-start-year={year}"
            f"&facet-end-year={year}"
        )
        if start_month is not None:
            url += f"&facet-start-month={start_month}"
        if end_month is not None:
            url += f"&facet-end-month={end_month}"
        if page > 1:
            url += f"&page={page}"
        return url

    # ── total count ───────────────────────────────────────────────────────────

    def _get_total(
        self,
        query: str,
        year: int,
        start_month: Optional[int],
        end_month: Optional[int],
    ) -> int:
        if self._http_works:
            try:
                url = self._build_url(query, year, start_month, end_month)
                resp = _SESSION.get(url, timeout=30)
                if resp.status_code == 200:
                    return self._extract_total_from_html(resp.text)
                if resp.status_code == 403:
                    self._http_works = False
                    print("  [Springer] HTTP blocked (403) — switching to Selenium")
            except Exception as exc:
                print(f"  [Springer] HTTP total count failed: {exc}")

        self._navigate_selenium(query, year, start_month, end_month)
        return self._get_total_selenium()

    def _extract_total_from_html(self, html_text: str) -> int:
        if _HAS_BS4:
            soup = BeautifulSoup(html_text, "html.parser")
            for sel in [
                "#number-of-search-results-and-search-terms",
                "h1.number-of-search-results",
                "strong.number-of-search-results",
                "[data-test='results-count']",
                "h1[id*='search-results']",
            ]:
                el = soup.select_one(sel)
                if el:
                    m = re.search(r"([\d,]+)", el.get_text())
                    if m:
                        return int(m.group(1).replace(",", ""))

        for pat in [
            r"([\d,]+)\s+results?\b",
            r"of\s+([\d,]+)\s+results?",
            r"Showing\s+\d+[-–]\d+\s+of\s+([\d,]+)",
        ]:
            m = re.search(pat, html_text, re.I)
            if m:
                val = int(m.group(1).replace(",", ""))
                if val > 0:
                    return val
        return 0

    # ══════════════════════════════════════════════════════════════════════════
    #  HTTP-BASED SCRAPING (primary)
    # ══════════════════════════════════════════════════════════════════════════

    def _scrape_all_pages_http(
        self,
        query: str,
        year: int,
        start_month: Optional[int],
        end_month: Optional[int],
    ) -> List[dict]:
        papers: List[dict] = []
        seen: set = set()
        page = 1
        max_pages = 100

        while page <= max_pages:
            try:
                url = self._build_url(
                    query, year, start_month, end_month, page
                )
                resp = _SESSION.get(url, timeout=30)

                if resp.status_code != 200:
                    if resp.status_code == 403:
                        self._http_works = False
                        print("  [Springer] HTTP blocked — switching to Selenium")
                    break

                page_papers = self._parse_page(resp.text, year, seen)

                if not page_papers:
                    break

                papers.extend(page_papers)
                print(
                    f"  [Springer] HTTP page {page}: "
                    f"+{len(page_papers)} (total {len(papers)})"
                )

                if not self._has_next_page_html(resp.text):
                    break

                page += 1
                random_delay(2, 4)

            except Exception as exc:
                print(f"  [Springer] HTTP page {page} failed: {exc}")
                break

        return papers

    # ── HTML parsing ──────────────────────────────────────────────────────────

    def _parse_page(
        self, html_text: str, year: int, seen: set
    ) -> List[dict]:
        if _HAS_BS4:
            return self._parse_page_bs4(html_text, year, seen)
        return self._parse_page_regex(html_text, year, seen)

    def _parse_page_bs4(
        self, html_text: str, year: int, seen: set
    ) -> List[dict]:
        soup = BeautifulSoup(html_text, "html.parser")
        papers: List[dict] = []

        # Strategy 1: structured items
        items = soup.select(
            "ol#results-list > li, "
            'li[data-test="search-result"], '
            "li.no-access, li.open-access, "
            "article.search-result-item, "
            "div.search-result"
        )
        if items:
            for item in items:
                paper = self._extract_item_bs4(item, year, seen)
                if paper:
                    papers.append(paper)
            if papers:
                return papers

        # Strategy 2: article links
        links = soup.select(
            'a[href*="/article/10."], a[href*="/chapter/10."]'
        )
        for a_tag in links:
            href = a_tag.get("href", "")
            title = html_mod.unescape(a_tag.get_text(strip=True))
            if not title or len(title) < 10 or href in seen:
                continue
            seen.add(href)
            doi = ""
            if "/article/" in href:
                doi = href.split("/article/")[-1].split("?")[0]
            elif "/chapter/" in href:
                doi = href.split("/chapter/")[-1].split("?")[0]
            link = (
                href
                if href.startswith("http")
                else f"https://link.springer.com{href}"
            )
            authors = self._find_authors_bs4(a_tag)
            yr = str(year)
            if a_tag.parent:
                ym = re.search(r"\b(20\d{2})\b", a_tag.parent.get_text())
                if ym:
                    yr = ym.group(1)
            papers.append(
                {
                    "Title": title,
                    "Authors": authors,
                    "Year": yr,
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
        a_tag = (
            item.select_one("a.title")
            or item.select_one("h2 a")
            or item.select_one("h3 a")
            or item.select_one('a[href*="/article/10."]')
            or item.select_one('a[href*="/chapter/10."]')
        )
        if not a_tag:
            return None

        href = a_tag.get("href", "")
        title = html_mod.unescape(a_tag.get_text(strip=True))
        if not title or len(title) < 10 or href in seen:
            return None
        seen.add(href)

        doi = ""
        if "/article/" in href:
            doi = href.split("/article/")[-1].split("?")[0]
        elif "/chapter/" in href:
            doi = href.split("/chapter/")[-1].split("?")[0]

        link = (
            href
            if href.startswith("http")
            else f"https://link.springer.com{href}"
        )

        author_els = item.select(
            '[itemprop="author"] a, '
            "span.authors a, "
            "span.search-result-authors a, "
            '[data-test="author-text"], '
            "span.c-author-list a, "
            "ul.c-author-list a"
        )
        authors = "; ".join(
            html_mod.unescape(a.get_text(strip=True))
            for a in author_els
            if a.get_text(strip=True)
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
        for _ in range(5):
            parent = parent.parent
            if parent is None:
                break
            authors = parent.select(
                '[itemprop="author"] a, '
                "span.authors a, "
                '[data-test="author-text"], '
                "span.c-author-list a"
            )
            if authors:
                return "; ".join(
                    html_mod.unescape(a.get_text(strip=True))
                    for a in authors
                )
        return ""

    def _has_next_page_html(self, html_text: str) -> bool:
        if _HAS_BS4:
            soup = BeautifulSoup(html_text, "html.parser")
            return bool(
                soup.select_one('a[rel="next"]')
                or soup.select_one("a.c-pagination__btn--next")
                or soup.select_one('[data-test="next-page"]')
                or soup.select_one("li.next a")
            )
        return bool(re.search(r'rel="next"', html_text))

    def _parse_page_regex(
        self, html_text: str, year: int, seen: set
    ) -> List[dict]:
        papers: List[dict] = []
        for pattern_str, kind in [
            (r'<a[^>]*href="(/article/10\.[^"]+)"[^>]*>\s*([^<]{10,}?)\s*</a>', "article"),
            (r'<a[^>]*href="(/chapter/10\.[^"]+)"[^>]*>\s*([^<]{10,}?)\s*</a>', "chapter"),
        ]:
            pattern = re.compile(pattern_str, re.I | re.S)
            for m in pattern.finditer(html_text):
                href = m.group(1)
                title = html_mod.unescape(m.group(2).strip())
                if href in seen or not title:
                    continue
                seen.add(href)
                doi = href.split(f"/{kind}/")[-1].split("?")[0]
                papers.append(
                    {
                        "Title": title,
                        "Authors": "",
                        "Year": str(year),
                        "Publisher": self.PUBLISHER,
                        "DOI": doi,
                        "Link": f"https://link.springer.com{href}",
                        "Source": self.SOURCE_NAME,
                    }
                )
        return papers

    # ══════════════════════════════════════════════════════════════════════════
    #  SELENIUM FALLBACK
    # ══════════════════════════════════════════════════════════════════════════

    def _navigate_selenium(
        self,
        query: str,
        year: int,
        start_month: Optional[int] = None,
        end_month: Optional[int] = None,
    ) -> None:
        url = self._build_url(query, year, start_month, end_month)
        self.driver.get(url)
        time.sleep(5)
        ensure_page_ready(self.driver, self._first_visit)
        self._first_visit = False

    def _get_total_selenium(self) -> int:
        time.sleep(2)
        try:
            body_text = self.driver.find_element(By.TAG_NAME, "body").text
            for pat in [
                r"([\d,]+)\s+results?\b",
                r"of\s+([\d,]+)\s+results?",
                r"Showing\s+\d+[-–]\d+\s+of\s+([\d,]+)",
            ]:
                m = re.search(pat, body_text, re.I)
                if m:
                    val = int(m.group(1).replace(",", ""))
                    if val > 0:
                        return val
        except Exception:
            pass

        selectors = [
            "//h1[contains(@class,'number-of-search-results')]",
            "//*[@id='number-of-search-results-and-search-terms']",
            "//strong[@class='number-of-search-results']",
            "//*[contains(@class,'search-results-number')]",
            "//*[@data-test='results-count']",
        ]
        for sel in selectors:
            try:
                el = self.driver.find_element(By.XPATH, sel)
                m = re.search(r"([\d,]+)", el.text)
                if m:
                    return int(m.group(1).replace(",", ""))
            except Exception:
                continue

        try:
            links = self.driver.find_elements(
                By.XPATH, "//a[contains(@href,'/article/10.')]"
            )
            if links:
                return len(links)
        except Exception:
            pass

        return 0

    def _scrape_all_pages_selenium(self) -> List[dict]:
        papers: List[dict] = []
        seen: set = set()
        page = 1

        while True:
            print(f"  Page {page} …")
            time.sleep(3)

            links = self.driver.find_elements(
                By.XPATH,
                "//a[contains(@href,'/article/10.')] | "
                "//a[contains(@href,'/chapter/10.')]",
            )

            if not links:
                if has_captcha(self.driver):
                    pause_for_human(
                        "CAPTCHA on Springer — solve it, then press ENTER."
                    )
                    continue
                break

            for el in links:
                try:
                    href = el.get_attribute("href")
                    if not href or href in seen:
                        continue
                    title = el.text.strip()
                    if not title or len(title) < 10:
                        continue
                    seen.add(href)
                    meta = self._extract_metadata_selenium(el)
                    doi = ""
                    if "/article/" in href:
                        doi = href.split("/article/")[-1].split("?")[0]
                    elif "/chapter/" in href:
                        doi = href.split("/chapter/")[-1].split("?")[0]
                    papers.append(
                        {
                            "Title": title,
                            "Authors": meta["authors"],
                            "Year": meta["year"],
                            "Publisher": self.PUBLISHER,
                            "DOI": doi,
                            "Link": href,
                            "Source": self.SOURCE_NAME,
                        }
                    )
                except Exception:
                    continue

            if not self._go_next_page_selenium():
                break
            page += 1
            random_delay(DELAY_BETWEEN_PAGES_MIN, DELAY_BETWEEN_PAGES_MAX)

        return papers

    @staticmethod
    def _extract_metadata_selenium(link_el) -> dict:
        meta = {"authors": "", "year": ""}
        try:
            parent = link_el
            for _ in range(5):
                parent = parent.find_element(By.XPATH, "..")
                if not meta["authors"]:
                    try:
                        aels = parent.find_elements(
                            By.XPATH,
                            ".//*[@itemprop='author']//a | "
                            ".//*[contains(@class,'author')]//a | "
                            ".//*[contains(@class,'c-author-list')]//a",
                        )
                        names = [
                            a.text.strip() for a in aels if a.text.strip()
                        ]
                        if names:
                            meta["authors"] = "; ".join(names)
                    except Exception:
                        pass
                if not meta["year"]:
                    m = re.search(r"\b(20\d{2})\b", parent.text)
                    if m:
                        meta["year"] = m.group(1)
        except Exception:
            pass
        return meta

    def _go_next_page_selenium(self) -> bool:
        try:
            btn = self.driver.find_element(
                By.XPATH,
                "//a[@rel='next'] | "
                "//a[@aria-label='Next page'] | "
                "//a[contains(@class,'c-pagination__btn--next')] | "
                "//a[contains(@class,'next')] | "
                "//button[contains(@class,'next')]",
            )
            classes = (btn.get_attribute("class") or "").lower()
            if (
                "disabled" in classes
                or btn.get_attribute("aria-disabled") == "true"
            ):
                return False
            self.driver.execute_script("arguments[0].scrollIntoView();", btn)
            time.sleep(1)
            self.driver.execute_script("arguments[0].click();", btn)
            return True
        except NoSuchElementException:
            return False


# ── stand-alone test ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    from config import REMEDIATION_STRINGS

    drv = setup_driver()
    try:
        scraper = SpringerScraper(drv)
        df = scraper.scrape(
            {"REM-1": REMEDIATION_STRINGS["REM-1"]}, 2023, 2023
        )
        print(df)
    finally:
        drv.quit()
