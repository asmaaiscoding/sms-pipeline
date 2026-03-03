"""
app/scrapers/springer.py
─────────────────────────
Scraper for Springer Link.

Primary: HTTP requests + HTML parsing (Springer does not use Cloudflare).
Fallback: Selenium headless.
Adaptive month-subdivision when a year returns > 1 000 results.
"""

from __future__ import annotations

import html as html_mod
import logging
import re
import time
from typing import Dict, List, Optional
from urllib.parse import quote_plus

import pandas as pd
import requests
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException

from app.scrapers.base import random_delay, ensure_page_ready, has_captcha, _get_null_logger

logger = logging.getLogger(__name__)

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

    def __init__(self, driver, flog: Optional[logging.Logger] = None):
        self.driver = driver
        self._first_visit = True
        self._http_works = True  # assume HTTP works until proven otherwise
        self.flog = flog or _get_null_logger()
        self.flog.info("[Springer] Scraper initialised  (BS4=%s, http_works=%s)", _HAS_BS4, self._http_works)

    # ── public entry point ────────────────────────────────────────────────────

    def scrape(
        self,
        search_strings: Dict[str, str],
        year_start: int,
        year_end: int,
    ) -> pd.DataFrame:
        all_papers: List[dict] = []
        self.flog.info("[Springer] scrape() called — %d search strings, years %d–%d",
                       len(search_strings), year_start, year_end)

        for year in range(year_start, year_end + 1):
            self.flog.info("[Springer] ── YEAR %d ──", year)
            try:
                year_papers = self._scrape_year(year, search_strings)
            except Exception as exc:
                self.flog.error("[Springer] _scrape_year(%d) EXCEPTION: %s", year, exc, exc_info=True)
                year_papers = []
            all_papers.extend(year_papers)
            self.flog.info("[Springer] Year %d done — %d papers (running total %d)", year, len(year_papers), len(all_papers))
            random_delay(2, 4)

        self.flog.info("[Springer] scrape() finished — total %d papers", len(all_papers))

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
        logger.info("[Springer] YEAR %d", year)
        year_papers: List[dict] = []

        for sname, query in search_strings.items():
            self.flog.info("[Springer] %s | year %d", sname, year)
            logger.info("[Springer] %s | year %d", sname, year)
            try:
                papers = self._scrape_adaptive(query, sname, year)
            except Exception as exc:
                self.flog.error("[Springer] _scrape_adaptive FAILED for %s/%d: %s", sname, year, exc, exc_info=True)
                papers = []
            for p in papers:
                p["Search_String"] = sname
            year_papers.extend(papers)
            self.flog.info("[Springer] %s | %d: %d papers", sname, year, len(papers))
            logger.info("[Springer] %s | %d: %d papers", sname, year, len(papers))
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

        self.flog.debug("[Springer-ADAPTIVE] query='%s' year=%d months=%s–%s",
                        sname, year, start_month, end_month)

        total = self._get_total(query, year, start_month, end_month)
        self.flog.debug("[Springer-ADAPTIVE] Total results: %d (threshold=%d)", total, MAX_RESULTS_THRESHOLD)

        if total > MAX_RESULTS_THRESHOLD and start_month != end_month:
            self.flog.info("[Springer-ADAPTIVE] %d results — subdividing months %d–%d", total, start_month, end_month)
            logger.info("  %d results — subdividing", total)
            papers: List[dict] = []
            if end_month - start_month + 1 == 12:
                for m in range(1, 13):
                    self.flog.debug("[Springer-ADAPTIVE] Sub-query: month %d", m)
                    papers.extend(
                        self._scrape_adaptive(query, sname, year, m, m)
                    )
                    time.sleep(2)
            else:
                mid = start_month + (end_month - start_month) // 2
                self.flog.debug("[Springer-ADAPTIVE] Splitting %d–%d into %d–%d and %d–%d",
                                start_month, end_month, start_month, mid, mid + 1, end_month)
                papers.extend(
                    self._scrape_adaptive(
                        query, sname, year, start_month, mid
                    )
                )
                time.sleep(2)
                papers.extend(
                    self._scrape_adaptive(
                        query, sname, year, mid + 1, end_month
                    )
                )
            self.flog.info("[Springer-ADAPTIVE] Subdivision done: %d papers for months %d–%d",
                           len(papers), start_month, end_month)
            return papers

        if total > MAX_RESULTS_THRESHOLD:
            self.flog.warning("[Springer-ADAPTIVE] %d results in single month range — some may be missed", total)
            logger.warning("  %d results in single month — some may be missed", total)

        if total == 0:
            self.flog.debug("[Springer-ADAPTIVE] 0 results — returning empty")
            return []

        # Scrape all pages
        if self._http_works:
            self.flog.debug("[Springer-ADAPTIVE] Trying HTTP scraping...")
            papers = self._scrape_all_pages_http(
                query, year, start_month, end_month
            )
            if papers:
                self.flog.info("[Springer-ADAPTIVE] HTTP succeeded: %d papers", len(papers))
                return papers
            self.flog.debug("[Springer-ADAPTIVE] HTTP returned 0 — falling back to Selenium")

        # Fallback to Selenium
        self.flog.debug("[Springer-ADAPTIVE] Using Selenium fallback...")
        self._navigate_selenium(query, year, start_month, end_month)
        papers = self._scrape_all_pages_selenium()
        self.flog.info("[Springer-ADAPTIVE] Selenium fallback: %d papers", len(papers))
        return papers

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
        """Get total result count. Try HTTP first, Selenium fallback."""
        if self._http_works:
            try:
                url = self._build_url(query, year, start_month, end_month)
                self.flog.debug("[Springer-TOTAL] HTTP GET %s", url)
                resp = _SESSION.get(url, timeout=30)
                self.flog.debug("[Springer-TOTAL] HTTP status=%d, length=%d", resp.status_code, len(resp.text))
                if resp.status_code == 200:
                    total = self._extract_total_from_html(resp.text)
                    self.flog.debug("[Springer-TOTAL] Extracted total: %d", total)
                    return total
                if resp.status_code == 403:
                    self._http_works = False
                    self.flog.warning("[Springer-TOTAL] HTTP 403 — switching to Selenium permanently")
                    logger.warning("[Springer] HTTP blocked (403) — switching to Selenium")
            except Exception as exc:
                self.flog.warning("[Springer-TOTAL] HTTP total count failed: %s", exc)
                logger.warning("[Springer] HTTP total count failed: %s", exc)

        # Selenium fallback
        self.flog.debug("[Springer-TOTAL] Using Selenium fallback for total count")
        self._navigate_selenium(query, year, start_month, end_month)
        total = self._get_total_selenium()
        self.flog.debug("[Springer-TOTAL] Selenium total: %d", total)
        return total

    def _extract_total_from_html(self, html_text: str) -> int:
        """Extract total result count from HTML."""
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

        # Regex fallback
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
        self.flog.debug("[Springer-HTTP] Starting HTTP pagination (max_pages=%d)", max_pages)

        while page <= max_pages:
            try:
                url = self._build_url(
                    query, year, start_month, end_month, page
                )
                self.flog.debug("[Springer-HTTP] GET page %d: %s", page, url)
                resp = _SESSION.get(url, timeout=30)

                self.flog.debug("[Springer-HTTP] Response: status=%d, length=%d", resp.status_code, len(resp.text))

                if resp.status_code != 200:
                    if resp.status_code == 403:
                        self._http_works = False
                        self.flog.warning("[Springer-HTTP] HTTP 403 — switching to Selenium permanently")
                        logger.warning("[Springer] HTTP blocked — switching to Selenium")
                    else:
                        self.flog.warning("[Springer-HTTP] HTTP %d — stopping", resp.status_code)
                    break

                page_papers = self._parse_page(resp.text, year, seen)
                self.flog.debug("[Springer-HTTP] Parsed page %d: %d papers", page, len(page_papers))

                if not page_papers:
                    self.flog.debug("[Springer-HTTP] No papers on page %d — stopping", page)
                    break

                papers.extend(page_papers)
                self.flog.info("[Springer-HTTP] Page %d: +%d (total %d)", page, len(page_papers), len(papers))
                logger.info("[Springer] HTTP page %d: +%d (total %d)", page, len(page_papers), len(papers))

                if not self._has_next_page_html(resp.text):
                    self.flog.debug("[Springer-HTTP] No next-page link — stopping")
                    break

                page += 1
                random_delay(2, 4)

            except Exception as exc:
                self.flog.error("[Springer-HTTP] Page %d EXCEPTION: %s", page, exc, exc_info=True)
                logger.warning("[Springer] HTTP page %d failed: %s", page, exc)
                break

        self.flog.info("[Springer-HTTP] Finished — %d papers across %d pages", len(papers), page)
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

        # Strategy 1: structured result items
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

        # Strategy 2: find all article links directly
        links = soup.select(
            'a[href*="/article/10."], '
            'a[href*="/chapter/10."]'
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
                year_match = re.search(
                    r"\b(20\d{2})\b", a_tag.parent.get_text()
                )
                if year_match:
                    yr = year_match.group(1)

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
        """Extract paper from a Springer search result item (BS4)."""
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

        # Authors — try multiple selectors matching Springer layouts
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

        # Year from item text
        yr = str(year)
        item_text = item.get_text()
        year_match = re.search(r"\b(20\d{2})\b", item_text)
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
        """Walk up from a title link to find author elements."""
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
        """Check if there's a next-page link in the HTML."""
        if _HAS_BS4:
            soup = BeautifulSoup(html_text, "html.parser")
            return bool(
                soup.select_one('a[rel="next"]')
                or soup.select_one("a.c-pagination__btn--next")
                or soup.select_one('[data-test="next-page"]')
                or soup.select_one("li.next a")
            )
        return bool(re.search(r'rel="next"', html_text))

    # ── Regex parsing fallback ────────────────────────────────────────────────

    def _parse_page_regex(
        self, html_text: str, year: int, seen: set
    ) -> List[dict]:
        papers: List[dict] = []

        # Pattern for article links: <a href="/article/10.xxxx/..." ...>Title</a>
        pattern = re.compile(
            r'<a[^>]*href="(/article/10\.[^"]+)"[^>]*>\s*'
            r"([^<]{10,}?)\s*</a>",
            re.I | re.S,
        )
        for m in pattern.finditer(html_text):
            href = m.group(1)
            title = html_mod.unescape(m.group(2).strip())

            if href in seen or not title:
                continue
            seen.add(href)

            doi = href.split("/article/")[-1].split("?")[0]
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

        # Also try chapter links
        chap_pattern = re.compile(
            r'<a[^>]*href="(/chapter/10\.[^"]+)"[^>]*>\s*'
            r"([^<]{10,}?)\s*</a>",
            re.I | re.S,
        )
        for m in chap_pattern.finditer(html_text):
            href = m.group(1)
            title = html_mod.unescape(m.group(2).strip())
            if href in seen or not title:
                continue
            seen.add(href)
            doi = href.split("/chapter/")[-1].split("?")[0]
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
        self.flog.debug("[Springer-SEL] Navigating to %s", url)
        try:
            self.driver.get(url)
            self.flog.debug("[Springer-SEL] Page loaded, current URL=%s", self.driver.current_url)
        except Exception as exc:
            self.flog.error("[Springer-SEL] driver.get() FAILED: %s", exc, exc_info=True)
        time.sleep(5)
        ensure_page_ready(self.driver, self._first_visit, flog=self.flog)
        self._first_visit = False

    def _get_total_selenium(self) -> int:
        time.sleep(2)
        self.flog.debug("[Springer-SEL-COUNT] Extracting total from Selenium page")

        # Strategy 1: body text
        try:
            body_text = self.driver.find_element(By.TAG_NAME, "body").text
            self.flog.debug("[Springer-SEL-COUNT] Body text length: %d chars", len(body_text))
            for pat in [
                r"([\d,]+)\s+results?\b",
                r"of\s+([\d,]+)\s+results?",
                r"Showing\s+\d+[-–]\d+\s+of\s+([\d,]+)",
            ]:
                m = re.search(pat, body_text, re.I)
                if m:
                    val = int(m.group(1).replace(",", ""))
                    if val > 0:
                        self.flog.debug("[Springer-SEL-COUNT] Strategy 1 (body regex): %d", val)
                        logger.debug("[Springer] total from body text: %d", val)
                        return val
        except Exception as exc:
            self.flog.debug("[Springer-SEL-COUNT] Strategy 1 failed: %s", exc)

        # Strategy 2: XPath selectors
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
                m = re.search(r"([\d,]+)", el.text, re.I)
                if m:
                    val = int(m.group(1).replace(",", ""))
                    self.flog.debug("[Springer-SEL-COUNT] Strategy 2 (selector '%s'): %d", sel[:40], val)
                    return val
            except Exception:
                continue

        # Strategy 3: count article links
        try:
            links = self.driver.find_elements(
                By.XPATH, "//a[contains(@href,'/article/10.')]"
            )
            if links:
                count = len(links)
                self.flog.debug("[Springer-SEL-COUNT] Strategy 3 (link count): %d", count)
                logger.debug("[Springer] No result count — %d article links present", count)
                return count
        except Exception as exc:
            self.flog.debug("[Springer-SEL-COUNT] Strategy 3 failed: %s", exc)

        self.flog.warning("[Springer-SEL-COUNT] All strategies failed — returning 0")
        return 0

    def _scrape_all_pages_selenium(self) -> List[dict]:
        papers: List[dict] = []
        seen: set = set()
        page = 1
        self.flog.debug("[Springer-SEL] Starting Selenium page scraping")

        while True:
            time.sleep(3)

            links = self.driver.find_elements(
                By.XPATH,
                "//a[contains(@href,'/article/10.')] | "
                "//a[contains(@href,'/chapter/10.')]",
            )
            self.flog.debug("[Springer-SEL] Page %d: found %d article/chapter links", page, len(links))

            if not links:
                if has_captcha(self.driver, flog=self.flog):
                    self.flog.warning("[Springer-SEL] CAPTCHA on page %d — waiting 30s", page)
                    logger.warning("CAPTCHA on Springer — waiting 30s")
                    time.sleep(30)
                    continue
                self.flog.debug("[Springer-SEL] No links and no CAPTCHA — done")
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
                    new_count += 1
                except Exception as exc:
                    self.flog.debug("[Springer-SEL] Error extracting paper: %s", exc)
                    continue

            self.flog.debug("[Springer-SEL] Page %d: +%d new papers (total %d)", page, new_count, len(papers))

            if not self._go_next_page_selenium():
                self.flog.debug("[Springer-SEL] No next page — stopping")
                break
            page += 1
            random_delay(2, 5)

        self.flog.info("[Springer-SEL] Pagination done: %d papers over %d pages", len(papers), page)
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
