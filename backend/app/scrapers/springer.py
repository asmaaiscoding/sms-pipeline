"""
app/scrapers/springer.py
─────────────────────────
Scraper for Springer Link (headless-compatible).
Adaptive month-subdivision when a year returns > 1 000 results.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Dict, List, Optional
from urllib.parse import quote_plus

import pandas as pd
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException

from app.scrapers.base import random_delay, ensure_page_ready, has_captcha

logger = logging.getLogger(__name__)

MAX_RESULTS_THRESHOLD = 1000


class SpringerScraper:
    """Scrape papers from Springer Link year-by-year with adaptive subdivision."""

    SOURCE_NAME = "Springer Link"
    PUBLISHER = "Springer"

    def __init__(self, driver):
        self.driver = driver
        self._first_visit = True

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
            return pd.DataFrame(columns=[
                "Title", "Authors", "Year", "Publisher",
                "DOI", "Link", "Source", "Search_String",
            ])
        return pd.DataFrame(all_papers)

    def _scrape_year(self, year: int, search_strings: Dict[str, str]) -> List[dict]:
        logger.info("[Springer] YEAR %d", year)
        year_papers: List[dict] = []

        for sname, query in search_strings.items():
            logger.info("[Springer] %s | year %d", sname, year)
            papers = self._scrape_adaptive(query, sname, year)
            for p in papers:
                p["Search_String"] = sname
            year_papers.extend(papers)
            logger.info("[Springer] %s | %d: %d papers", sname, year, len(papers))
            time.sleep(3)

        return year_papers

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

        self._navigate(query, year, year, start_month, end_month)
        total = self._get_total_results()

        if total > MAX_RESULTS_THRESHOLD and start_month != end_month:
            logger.info("  %d results — subdividing", total)
            papers: List[dict] = []
            if end_month - start_month + 1 == 12:
                for m in range(1, 13):
                    papers.extend(self._scrape_adaptive(query, sname, year, m, m))
                    time.sleep(2)
            else:
                mid = start_month + (end_month - start_month) // 2
                papers.extend(self._scrape_adaptive(query, sname, year, start_month, mid))
                time.sleep(2)
                papers.extend(self._scrape_adaptive(query, sname, year, mid + 1, end_month))
            return papers

        if total > MAX_RESULTS_THRESHOLD:
            logger.warning("  %d results in single month — some may be missed", total)

        return self._scrape_all_pages()

    def _navigate(
        self,
        query: str,
        start_year: int,
        end_year: int,
        start_month: Optional[int] = None,
        end_month: Optional[int] = None,
    ) -> None:
        encoded = quote_plus(query)
        url = (
            f"https://link.springer.com/search"
            f"?query={encoded}"
            f"&facet-content-type=Article"
            f"&date-facet-mode=between"
            f"&facet-start-year={start_year}"
            f"&facet-end-year={end_year}"
        )
        if start_month is not None:
            url += f"&facet-start-month={start_month}"
        if end_month is not None:
            url += f"&facet-end-month={end_month}"

        self.driver.get(url)
        time.sleep(5)
        ensure_page_ready(self.driver, self._first_visit)
        self._first_visit = False

    def _get_total_results(self) -> int:
        time.sleep(2)

        # Strategy 1: look for body text like "1,234 results" or "Showing 1-20 of 1,234"
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
                        logger.debug("[Springer] total from body text: %d", val)
                        return val
        except Exception:
            pass

        # Strategy 2: XPath selectors for known Springer elements
        selectors = [
            "//h1[contains(@class,'number-of-search-results')]",
            "//h1[contains(text(),'result')]",
            "//*[@id='number-of-search-results-and-search-terms']",
            "//strong[@class='number-of-search-results']",
            "//*[contains(@class,'search-results-number')]",
        ]
        for sel in selectors:
            try:
                el = self.driver.find_element(By.XPATH, sel)
                m = re.search(r"([\d,]+)\s*result", el.text, re.I)
                if m:
                    return int(m.group(1).replace(",", ""))
            except Exception:
                continue

        # Strategy 3: count article links on page as minimum estimate
        try:
            links = self.driver.find_elements(
                By.XPATH, "//a[contains(@href,'/article/10.')]"
            )
            if links:
                count = len(links)
                logger.debug("[Springer] No result count found, but %d article links present", count)
                return count
        except Exception:
            pass

        return 0

    def _scrape_all_pages(self) -> List[dict]:
        papers: List[dict] = []
        seen: set = set()
        page = 1

        while True:
            logger.debug("  Page %d", page)
            time.sleep(3)

            links = self.driver.find_elements(
                By.XPATH, "//a[contains(@href,'/article/10.')]"
            )

            if not links:
                if has_captcha(self.driver):
                    logger.warning("CAPTCHA on Springer — waiting")
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
                    if "/article/" in href:
                        doi = href.split("/article/")[-1].split("?")[0]

                    papers.append({
                        "Title": title,
                        "Authors": meta["authors"],
                        "Year": meta["year"],
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
                        aels = parent.find_elements(
                            By.XPATH,
                            ".//*[@itemprop='author']//a | .//*[contains(@class,'author')]//a",
                        )
                        names = [a.text.strip() for a in aels if a.text.strip()]
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

    def _go_next_page(self) -> bool:
        try:
            btn = self.driver.find_element(
                By.XPATH,
                "//a[@rel='next'] | //a[@aria-label='Next page'] | "
                "//button[contains(@class,'next')] | //a[contains(@class,'next')]",
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
