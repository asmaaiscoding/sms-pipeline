"""
scrapers/ieee.py
----------------
Scraper for IEEE Xplore.

Usage (stand-alone test):
    python -m scrapers.ieee
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import pandas as pd
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from scrapers.base import (
    setup_driver,
    random_delay,
    ensure_page_ready,
    has_captcha,
    pause_for_human,
    retry,
)
from config import DELAY_BETWEEN_PAGES_MIN, DELAY_BETWEEN_PAGES_MAX, MAX_RETRIES


def _paper_hash(title: str, year: str) -> str:
    return hashlib.md5(f"{title.lower().strip()}_{year}".encode()).hexdigest()


class IEEEScraper:
    """Scrape papers from IEEE Xplore year-by-year."""

    SOURCE_NAME = "IEEE Xplore"
    PUBLISHER   = "IEEE"

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
            random_delay(3, 5)

        if not all_papers:
            return pd.DataFrame(columns=["Title", "Authors", "Year", "Publisher",
                                          "DOI", "Link", "Source", "Search_String"])
        return pd.DataFrame(all_papers)

    # ── per-year logic ────────────────────────────────────────────────────────

    def _scrape_year(self, year: int, search_strings: Dict[str, str]) -> List[dict]:
        print(f"\n{'=' * 60}")
        print(f"[IEEE] YEAR {year}")
        print(f"{'=' * 60}")

        year_papers: List[dict] = []

        for sname, query in search_strings.items():
            print(f"\n[IEEE] {sname} | year {year}")
            try:
                expected = retry(self._navigate, MAX_RETRIES, query, year)
            except Exception as exc:
                print(f"[IEEE] Navigation failed after retries: {exc}")
                continue

            if expected == 0:
                print(f"[IEEE] {sname} | {year}: 0 results — skipping")
                continue

            try:
                papers = retry(self._scrape_all_pages, MAX_RETRIES, year, expected)
            except Exception as exc:
                print(f"[IEEE] Scraping failed after retries: {exc}")
                continue

            for p in papers:
                p["Search_String"] = sname
            year_papers.extend(papers)
            print(f"[IEEE] {sname} | {year}: {len(papers)} papers")
            random_delay(3, 5)

        # deduplicate within the year
        return self._dedup(year_papers)

    # ── navigation ────────────────────────────────────────────────────────────

    def _navigate(self, query: str, year: int) -> int:
        """Navigate to IEEE search results for *query* × *year*.
        Returns the expected total result count (0 if none).
        """
        encoded = quote_plus(query)
        url = (
            f"https://ieeexplore.ieee.org/search/searchresult.jsp"
            f"?queryText={encoded}"
            f"&ranges={year}_{year}_Year"
        )
        self.driver.get(url)
        random_delay(4, 6)
        ensure_page_ready(self.driver, self._first_visit)
        self._first_visit = False

        # check for "no results"
        try:
            elems = self.driver.find_elements(
                By.XPATH,
                "//*[contains(text(),'No results found')] | "
                "//*[contains(text(),'0 results')] | "
                "//*[contains(text(),'No records found')]",
            )
            if elems and any(e.is_displayed() for e in elems):
                return 0
        except Exception:
            pass

        return self._get_total_count() or 0

    def _get_total_count(self) -> Optional[int]:
        selectors = [
            "//span[contains(@class,'result-count')] | //span[contains(@class,'results-count')]",
            "//div[contains(@class,'Dashboard-header')]//span[contains(text(),'results')]",
            "//*[contains(text(),'results') or contains(text(),'Results')]",
        ]
        for sel in selectors:
            try:
                el = self.driver.find_element(By.XPATH, sel)
                text = el.text
                m = re.search(r"of\s+([\d,]+)", text, re.I) or re.search(r"([\d,]+)\s+results?", text, re.I)
                if m:
                    return int(m.group(1).replace(",", ""))
            except Exception:
                continue
        return None

    # ── page scraping ─────────────────────────────────────────────────────────

    def _scrape_all_pages(self, target_year: int, expected: int) -> List[dict]:
        if expected == 0:
            return []

        papers: List[dict] = []
        seen_hashes: set = set()
        seen_links: set = set()
        page = 1
        stale_pages = 0
        max_stale = 3

        while True:
            print(f"  Page {page} …")
            random_delay(2, 4)

            # wait for results
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((
                        By.XPATH,
                        "//div[contains(@class,'List-results-items')] | //xpl-results-item",
                    ))
                )
            except TimeoutException:
                if has_captcha(self.driver):
                    pause_for_human("CAPTCHA on IEEE — solve it, then press ENTER.")
                    continue
                if page > 1:
                    break
                time.sleep(5)
                continue

            results = self.driver.find_elements(
                By.XPATH,
                "//xpl-results-item | "
                "//div[contains(@class,'List-results-items')]"
                "//div[contains(@class,'result-item-align')] | "
                "//div[@class='result-item']",
            )

            if not results:
                stale_pages += 1
                if stale_pages >= max_stale:
                    break
                continue

            new_count = 0
            for res in results:
                paper = self._extract_paper(res, target_year)
                if not paper or len(paper["Title"]) < 10:
                    continue
                h = _paper_hash(paper["Title"], paper["Year"])
                if h in seen_hashes or paper["Link"] in seen_links:
                    continue
                seen_hashes.add(h)
                seen_links.add(paper["Link"])
                papers.append(paper)
                new_count += 1

            print(f"  Found {new_count} new papers (total {len(papers)})")

            if expected and len(papers) >= expected:
                break
            if new_count == 0:
                stale_pages += 1
                if stale_pages >= max_stale:
                    break
            else:
                stale_pages = 0

            if not self._go_next_page(page):
                break
            page += 1

        return papers

    # ── single-paper extraction ───────────────────────────────────────────────

    def _extract_paper(self, el, target_year: int) -> Optional[dict]:
        paper = {
            "Title": "", "Link": "", "Authors": "",
            "Year": str(target_year), "Publisher": self.PUBLISHER,
            "DOI": "", "Source": self.SOURCE_NAME,
        }
        try:
            title_el = el.find_element(
                By.XPATH,
                ".//h2[@class='text-md-md-lh']//a | .//h3//a | "
                ".//a[contains(@class,'result-item-title')]",
            )
            paper["Title"] = title_el.text.strip()
            paper["Link"]  = title_el.get_attribute("href")
            if "/document/" in paper["Link"]:
                doc = paper["Link"].split("/document/")[-1].split("/")[0].split("?")[0]
                if doc.isdigit():
                    paper["DOI"] = f"10.1109/{doc}"
        except Exception:
            return None

        # authors
        try:
            aels = el.find_elements(
                By.XPATH,
                ".//p[@class='author']//a | "
                ".//span[contains(@class,'author')]//a | "
                ".//*[contains(@class,'author-name')]",
            )
            names = [a.text.strip() for a in aels if a.text.strip()]
            paper["Authors"] = "; ".join(names)
        except Exception:
            pass

        # publication
        try:
            pub = el.find_element(
                By.XPATH,
                ".//div[contains(@class,'description')]//a | "
                ".//span[contains(@class,'publisher-info-container')]//a",
            )
            paper["Publisher"] = pub.text.strip() or self.PUBLISHER
        except Exception:
            pass

        # year from metadata
        yr = self._extract_year(el)
        if yr:
            paper["Year"] = yr

        return paper

    @staticmethod
    def _extract_year(el) -> Optional[str]:
        patterns = [
            r"Date of Publication:\s*(\d{4})",
            r"Published in:\s*.*?(\d{4})",
        ]
        text = el.text
        for pat in patterns:
            m = re.search(pat, text, re.I)
            if m and 1900 <= int(m.group(1)) <= 2030:
                return m.group(1)
        try:
            desc = el.find_element(
                By.XPATH,
                ".//*[contains(@class,'publisher-info-container')] | .//*[contains(@class,'description')]",
            )
            for yr in re.findall(r"\b(19\d{2}|20\d{2})\b", desc.text):
                if 1900 <= int(yr) <= 2030:
                    return yr
        except Exception:
            pass
        return None

    # ── pagination ────────────────────────────────────────────────────────────

    def _go_next_page(self, current_page: int) -> bool:
        btn = None
        strategies = [
            # 1. aria-label
            "//button[@aria-label='Next page'] | //a[@aria-label='Next page'] | "
            "//button[@aria-label='next page'] | //a[@aria-label='next page']",
            # 2. class-based
            "//button[contains(@class,'stats-Pagination_arrow_next')] | "
            "//button[contains(@class,'Pagination') and contains(@class,'next')]",
        ]
        for sel in strategies:
            try:
                btn = self.driver.find_element(By.XPATH, sel)
                break
            except NoSuchElementException:
                continue

        # 3. inside paginator container
        if btn is None:
            try:
                container = self.driver.find_element(
                    By.XPATH, "//xpl-paginator | //div[contains(@class,'pagination')]"
                )
                btn = container.find_element(
                    By.XPATH,
                    ".//*[contains(@aria-label,'next') or contains(@aria-label,'Next')] | "
                    ".//button[contains(@class,'next') and not(contains(@class,'previous'))]",
                )
            except Exception:
                pass

        if btn is None:
            return False

        # validate
        classes = (btn.get_attribute("class") or "").lower()
        if "previous" in classes:
            return False
        href = btn.get_attribute("href") or ""
        if "/document/" in href:
            return False
        if btn.get_attribute("disabled") == "true" or btn.get_attribute("aria-disabled") == "true":
            return False
        if "disabled" in classes:
            return False

        old_url = self.driver.current_url
        self.driver.execute_script(
            "arguments[0].scrollIntoView({behavior:'smooth',block:'center'});", btn
        )
        random_delay(1, 2)
        self.driver.execute_script("arguments[0].click();", btn)
        random_delay(DELAY_BETWEEN_PAGES_MIN, DELAY_BETWEEN_PAGES_MAX)

        new_url = self.driver.current_url
        if "/document/" in new_url:
            return False
        if new_url == old_url:
            time.sleep(3)
            if self.driver.current_url == old_url:
                return False
        return True

    # ── dedup within year ─────────────────────────────────────────────────────

    @staticmethod
    def _dedup(papers: List[dict]) -> List[dict]:
        seen: set = set()
        unique: list = []
        for p in papers:
            h = _paper_hash(p["Title"], p["Year"])
            if h not in seen:
                seen.add(h)
                unique.append(p)
        removed = len(papers) - len(unique)
        if removed:
            print(f"  [IEEE] Removed {removed} intra-year duplicates")
        return unique


# ── stand-alone test ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    from config import DETECTION_STRINGS

    drv = setup_driver()
    try:
        scraper = IEEEScraper(drv)
        df = scraper.scrape({"DET-1": DETECTION_STRINGS["DET-1"]}, 2023, 2023)
        print(df)
    finally:
        drv.quit()
