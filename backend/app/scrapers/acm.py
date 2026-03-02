"""
app/scrapers/acm.py
───────────────────
Scraper for ACM Digital Library.

ACM uses Cloudflare bot protection which blocks headless browsers.
This scraper uses the ACM OpenURL/REST API as a fallback to avoid Cloudflare.
If the API doesn't work, it falls back to Selenium (which may be blocked).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Optional
from urllib.parse import quote_plus

import pandas as pd
import requests
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException

from app.scrapers.base import random_delay, ensure_page_ready, has_captcha

logger = logging.getLogger(__name__)

# ── HTTP session for API-based scraping ───────────────────────────────────────
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
})


class ACMScraper:
    """Scrape papers from ACM Digital Library year-by-year.

    Primary approach: use ACM's search results page.
    Cloudflare may block headless Selenium, so we try a requests-based
    approach first (parsing the exported BibTeX/search result pages).
    """

    SOURCE_NAME = "ACM Digital Library"
    PUBLISHER = "ACM"

    def __init__(self, driver):
        self.driver = driver
        self._first_visit = True
        self._cloudflare_blocked = False

    def scrape(
        self,
        search_strings: Dict[str, str],
        year_start: int,
        year_end: int,
    ) -> pd.DataFrame:
        all_papers: List[dict] = []
        api_blocked = False

        for sname, query in search_strings.items():
            for year in range(year_start, year_end + 1):
                logger.info("[ACM] %s | year %d", sname, year)

                papers: List[dict] = []

                # Try API-based approach first (unless we know it's blocked)
                if not api_blocked:
                    papers = self._scrape_via_api(query, sname, year)
                    if not papers:
                        api_blocked = True  # don't retry on every query

                # Fallback to Selenium if API returned nothing and not blocked
                if not papers and not self._cloudflare_blocked:
                    self._navigate(query, year)
                    if has_captcha(self.driver):
                        logger.warning(
                            "[ACM] Cloudflare is blocking both HTTP requests and headless Chrome. "
                            "ACM Digital Library cannot be scraped in this environment. "
                            "Consider using a VPN or running outside Docker."
                        )
                        self._cloudflare_blocked = True
                    else:
                        papers = self._scrape_all_pages(sname, year)

                if not papers and self._cloudflare_blocked:
                    logger.warning("[ACM] %s | %d: SKIPPED (Cloudflare block)", sname, year)
                else:
                    for p in papers:
                        p["Search_String"] = sname
                    all_papers.extend(papers)
                    logger.info("[ACM] %s | %d: %d papers", sname, year, len(papers))

                random_delay(1, 2)

        if self._cloudflare_blocked and not all_papers:
            logger.error(
                "[ACM] ⚠ All ACM queries were blocked by Cloudflare. "
                "0 papers collected from ACM Digital Library."
            )

        if not all_papers:
            return pd.DataFrame(columns=[
                "Title", "Authors", "Year", "Publisher",
                "DOI", "Link", "Source", "Search_String",
            ])
        return pd.DataFrame(all_papers)

    # ── API-based scraping ────────────────────────────────────────────────────

    def _scrape_via_api(self, query: str, sname: str, year: int) -> List[dict]:
        """Try to scrape ACM using direct HTTP requests to the search REST API."""
        papers: List[dict] = []
        page_size = 50
        start_page = 0
        max_pages = 20  # safety limit

        for page_num in range(max_pages):
            offset = page_num * page_size
            try:
                url = (
                    f"https://dl.acm.org/action/doSearch"
                    f"?fillQuickSearch=false&target=advanced"
                    f"&AllField={quote_plus(query)}"
                    f"&AfterYear={year}&BeforeYear={year}"
                    f"&startPage={page_num}&pageSize={page_size}"
                )
                resp = _SESSION.get(url, timeout=30)

                if resp.status_code == 403 or "just a moment" in resp.text.lower():
                    logger.warning("[ACM] API request blocked by Cloudflare (HTTP %d)", resp.status_code)
                    return papers

                if resp.status_code != 200:
                    logger.warning("[ACM] API HTTP %d", resp.status_code)
                    return papers

                html = resp.text
                page_papers = self._parse_search_html(html, year)

                if not page_papers:
                    break

                papers.extend(page_papers)
                logger.info("[ACM] API page %d: +%d papers (total %d)", page_num + 1, len(page_papers), len(papers))

                # Check if there are more pages
                if len(page_papers) < page_size:
                    break

                random_delay(2, 4)

            except Exception as exc:
                logger.warning("[ACM] API request failed: %s", exc)
                break

        return papers

    def _parse_search_html(self, html: str, year: int) -> List[dict]:
        """Parse paper data from ACM search results HTML."""
        papers: List[dict] = []

        # Find all DOI links in search results
        # Pattern: <a href="/doi/10.xxxx/yyyy" ...>Title</a>
        doi_pattern = re.compile(
            r'<h5[^>]*class="issue-item__title"[^>]*>\s*'
            r'<a[^>]*href="(/doi/[^"]+)"[^>]*>\s*([^<]+)\s*</a>',
            re.I | re.S,
        )

        # Also try a broader pattern
        alt_pattern = re.compile(
            r'<a[^>]*href="(/doi/10\.[^"]+)"[^>]*class="[^"]*hlFld-Title[^"]*"[^>]*>\s*([^<]+)',
            re.I | re.S,
        )

        # Yet another ACM layout pattern
        span_pattern = re.compile(
            r'<span[^>]*class="[^"]*hlFld-Title[^"]*"[^>]*>.*?'
            r'<a[^>]*href="(/doi/10\.[^"]+)"[^>]*>\s*([^<]+)',
            re.I | re.S,
        )

        seen: set = set()
        for pattern in [doi_pattern, alt_pattern, span_pattern]:
            for match in pattern.finditer(html):
                doi_path = match.group(1).strip()
                title = match.group(2).strip()

                if not title or len(title) < 10 or doi_path in seen:
                    continue
                seen.add(doi_path)

                doi = doi_path.replace("/doi/", "").split("?")[0]
                link = f"https://dl.acm.org{doi_path}"

                papers.append({
                    "Title": title,
                    "Authors": self._extract_authors_from_html(html, doi_path),
                    "Year": str(year),
                    "Publisher": self.PUBLISHER,
                    "DOI": doi,
                    "Link": link,
                    "Source": self.SOURCE_NAME,
                })

        return papers

    def _extract_authors_from_html(self, html: str, doi_path: str) -> str:
        """Try to extract authors near a given DOI link in the HTML."""
        try:
            idx = html.find(doi_path)
            if idx < 0:
                return ""
            # Look in a window after the DOI link for author spans
            window = html[idx:idx + 2000]
            author_pattern = re.compile(
                r'<span[^>]*class="[^"]*hlFld-ContribAuthor[^"]*"[^>]*>\s*'
                r'<a[^>]*>\s*([^<]+)',
                re.I | re.S,
            )
            authors = [m.group(1).strip() for m in author_pattern.finditer(window)]
            return "; ".join(authors[:20])
        except Exception:
            return ""

    # ── Selenium-based scraping (fallback) ────────────────────────────────────

    def _navigate(self, query: str, year: int) -> None:
        encoded = quote_plus(query)
        url = (
            f"https://dl.acm.org/action/doSearch"
            f"?fillQuickSearch=false&target=advanced"
            f"&AllField={encoded}"
            f"&AfterYear={year}&BeforeYear={year}"
        )
        self.driver.get(url)
        time.sleep(5)
        ensure_page_ready(self.driver, self._first_visit)
        self._first_visit = False

    def _scrape_all_pages(self, sname: str, year: int) -> List[dict]:
        papers: List[dict] = []
        seen: set = set()
        page = 1

        while True:
            logger.debug("  Page %d", page)
            time.sleep(3)

            links = self.driver.find_elements(
                By.XPATH, "//a[contains(@href, '/doi/10.')]"
            )

            if not links:
                if has_captcha(self.driver):
                    logger.warning("CAPTCHA detected on ACM — retrying after delay")
                    time.sleep(30)
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
                    meta = self._extract_metadata(el)

                    doi = ""
                    if "/doi/" in href:
                        doi = href.split("/doi/")[-1].split("?")[0]

                    papers.append({
                        "Title": title,
                        "Authors": meta["authors"],
                        "Year": meta["year"] or str(year),
                        "Publisher": self.PUBLISHER,
                        "DOI": doi,
                        "Link": href,
                        "Source": self.SOURCE_NAME,
                    })
                except Exception:
                    continue

            if not self._go_next_page():
                break
            page += 1
            random_delay(2, 5)

        return papers

    @staticmethod
    def _extract_metadata(link_el) -> dict:
        meta = {"authors": "", "year": "", "publication": ""}
        try:
            parent = link_el
            for _ in range(5):
                parent = parent.find_element(By.XPATH, "..")
                if not meta["authors"]:
                    try:
                        els = parent.find_elements(
                            By.XPATH,
                            ".//*[contains(@class,'author')]//a | "
                            ".//ul[contains(@aria-label,'author')]//a | "
                            ".//li[contains(@class,'loa')]//a",
                        )
                        names = [a.text.strip() for a in els if a.text.strip() and len(a.text.strip()) > 2]
                        if names:
                            meta["authors"] = "; ".join(names[:20])
                    except Exception:
                        pass
                if not meta["year"]:
                    m = re.search(r"\b(19|20)\d{2}\b", parent.text)
                    if m:
                        meta["year"] = m.group(0)
        except Exception:
            pass
        return meta

    def _go_next_page(self) -> bool:
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
            return True
        except NoSuchElementException:
            return False
