"""
scrapers/acm.py
---------------
Scraper for ACM Digital Library.

Usage (stand-alone test):
    python -m scrapers.acm
"""

from __future__ import annotations

import re
import time
from typing import Dict, List
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


class ACMScraper:
    """Scrape papers from ACM Digital Library year-by-year."""

    SOURCE_NAME = "ACM Digital Library"
    PUBLISHER   = "ACM"

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
        """Return a DataFrame with all papers found across strings × years."""
        all_papers: List[dict] = []

        for sname, query in search_strings.items():
            for year in range(year_start, year_end + 1):
                print(f"\n[ACM] {sname} | year {year}")
                self._navigate(query, year)
                papers = self._scrape_all_pages(sname, year)

                for p in papers:
                    p["Search_String"] = sname
                all_papers.extend(papers)

                print(f"[ACM] {sname} | {year}: {len(papers)} papers")
                random_delay(2, 4)

        if not all_papers:
            return pd.DataFrame(columns=["Title", "Authors", "Year", "Publisher",
                                          "DOI", "Link", "Source", "Search_String"])
        return pd.DataFrame(all_papers)

    # ── navigation ────────────────────────────────────────────────────────────

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

    # ── page scraping ─────────────────────────────────────────────────────────

    def _scrape_all_pages(self, sname: str, year: int) -> List[dict]:
        papers: List[dict] = []
        seen: set = set()
        page = 1

        while True:
            print(f"  Page {page} …")
            time.sleep(3)

            links = self.driver.find_elements(
                By.XPATH, "//a[contains(@href, '/doi/10.')]"
            )

            if not links:
                if has_captcha(self.driver):
                    pause_for_human("CAPTCHA detected on ACM — solve it, then press ENTER.")
                    continue            # retry same page
                print("  No results on this page.")
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
                        "Title":     title,
                        "Authors":   meta["authors"],
                        "Year":      meta["year"] or str(year),
                        "Publisher": self.PUBLISHER,
                        "DOI":       doi,
                        "Link":      href,
                        "Source":    self.SOURCE_NAME,
                    })
                except Exception:
                    continue

            # pagination
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
                # authors
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
                # publication
                if not meta["publication"]:
                    try:
                        pubs = parent.find_elements(
                            By.XPATH,
                            ".//span[@class='epub-section__title'] | "
                            ".//div[contains(@class,'issue-item__detail')]//a | "
                            ".//cite | .//em",
                        )
                        for p in pubs:
                            if p.text.strip() and len(p.text.strip()) > 3:
                                meta["publication"] = p.text.strip()
                                break
                    except Exception:
                        pass
                # year
                if not meta["year"]:
                    m = re.search(r"\b(19|20)\d{2}\b", parent.text)
                    if m:
                        meta["year"] = m.group(0)
        except Exception:
            pass
        return meta

    # ── pagination ────────────────────────────────────────────────────────────

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
