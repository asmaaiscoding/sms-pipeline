"""
app/scrapers/acm.py
───────────────────
Scraper for ACM Digital Library (headless-compatible).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Dict, List
from urllib.parse import quote_plus

import pandas as pd
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException

from app.scrapers.base import random_delay, ensure_page_ready, has_captcha

logger = logging.getLogger(__name__)


class ACMScraper:
    """Scrape papers from ACM Digital Library year-by-year."""

    SOURCE_NAME = "ACM Digital Library"
    PUBLISHER = "ACM"

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

        for sname, query in search_strings.items():
            for year in range(year_start, year_end + 1):
                logger.info("[ACM] %s | year %d", sname, year)
                self._navigate(query, year)
                papers = self._scrape_all_pages(sname, year)

                for p in papers:
                    p["Search_String"] = sname
                all_papers.extend(papers)

                logger.info("[ACM] %s | %d: %d papers", sname, year, len(papers))
                random_delay(2, 4)

        if not all_papers:
            return pd.DataFrame(columns=[
                "Title", "Authors", "Year", "Publisher",
                "DOI", "Link", "Source", "Search_String",
            ])
        return pd.DataFrame(all_papers)

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
