"""
scrapers/springer.py
--------------------
Scraper for Springer Link.
Includes adaptive month-subdivision when a year returns > 1 000 results.

Usage (stand-alone test):
    python -m scrapers.springer
"""

from __future__ import annotations

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
)
from config import DELAY_BETWEEN_PAGES_MIN, DELAY_BETWEEN_PAGES_MAX

MAX_RESULTS_THRESHOLD = 1000   # subdivide into months when exceeded


class SpringerScraper:
    """Scrape papers from Springer Link year-by-year with adaptive subdivision."""

    SOURCE_NAME = "Springer Link"
    PUBLISHER   = "Springer"

    def __init__(self, driver):
        self.driver = driver
        self._first_visit = True

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
            return pd.DataFrame(columns=["Title", "Authors", "Year", "Publisher",
                                          "DOI", "Link", "Source", "Search_String"])
        return pd.DataFrame(all_papers)

    def _scrape_year(self, year: int, search_strings: Dict[str, str]) -> List[dict]:
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
            period = f"year {year}"
        else:
            period = f"{year}-{start_month:02d} to {year}-{end_month:02d}"

        self._navigate(query, year, year, start_month, end_month)
        total = self._get_total_results()

        # subdivide if too many results and range can be split
        if total > MAX_RESULTS_THRESHOLD and start_month != end_month:
            print(f"  {total:,} results — subdividing {period}")
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
            print(f"  Warning: {total:,} results in single month — some may be missed")

        return self._scrape_all_pages()

    # ── navigation ────────────────────────────────────────────────────────────

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

    # ── result count ──────────────────────────────────────────────────────────

    def _get_total_results(self) -> int:
        time.sleep(2)
        selectors = [
            "//h1[contains(@class,'number-of-search-results')]",
            "//h1[contains(text(),'result')]",
            "//*[@id='number-of-search-results-and-search-terms']",
            "//strong[@class='number-of-search-results']",
        ]
        for sel in selectors:
            try:
                el = self.driver.find_element(By.XPATH, sel)
                m = re.search(r"([\d,]+)\s*result", el.text, re.I)
                if m:
                    return int(m.group(1).replace(",", ""))
            except Exception:
                continue
        return 0

    # ── page scraping ─────────────────────────────────────────────────────────

    def _scrape_all_pages(self) -> List[dict]:
        papers: List[dict] = []
        seen: set = set()
        page = 1

        while True:
            print(f"  Page {page} …")
            time.sleep(3)

            links = self.driver.find_elements(
                By.XPATH, "//a[contains(@href,'/article/10.')]"
            )

            if not links:
                if has_captcha(self.driver):
                    pause_for_human("CAPTCHA on Springer — solve it, then press ENTER.")
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
                        "Title":     title,
                        "Authors":   meta["authors"],
                        "Year":      meta["year"],
                        "Publisher": self.PUBLISHER,
                        "DOI":       doi,
                        "Link":      href,
                        "Source":    self.SOURCE_NAME,
                    })
                except Exception:
                    continue

            if not self._go_next_page():
                break
            page += 1
            random_delay(DELAY_BETWEEN_PAGES_MIN, DELAY_BETWEEN_PAGES_MAX)

        return papers

    # ── metadata extraction ───────────────────────────────────────────────────

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
                if not meta["publication"]:
                    try:
                        pubs = parent.find_elements(
                            By.XPATH,
                            ".//i[@data-test='journal-title'] | "
                            ".//span[@data-test='journal-title'] | .//cite | .//em",
                        )
                        for p in pubs:
                            if p.text.strip() and len(p.text.strip()) > 3:
                                meta["publication"] = p.text.strip()
                                break
                    except Exception:
                        pass
                if not meta["year"]:
                    m = re.search(r"\b(20\d{2})\b", parent.text)
                    if m:
                        meta["year"] = m.group(1)
        except Exception:
            pass
        return meta

    # ── pagination ────────────────────────────────────────────────────────────

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


# ── stand-alone test ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    from config import REMEDIATION_STRINGS

    drv = setup_driver()
    try:
        scraper = SpringerScraper(drv)
        df = scraper.scrape({"REM-1": REMEDIATION_STRINGS["REM-1"]}, 2023, 2023)
        print(df)
    finally:
        drv.quit()
