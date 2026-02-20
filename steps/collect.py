"""
steps/collect.py
----------------
Step 1 — Paper collection orchestrator.

Runs the selected scrapers (ACM, IEEE, Springer) for the given search
strings / year range and writes:
  - One xlsx per source   (e.g. output/acm_papers.xlsx)
  - One combined xlsx     (output/all_collected.xlsx)
"""

from __future__ import annotations

import os
from typing import Dict, List

import pandas as pd

from scrapers.base import setup_driver
from scrapers.acm import ACMScraper
from scrapers.ieee import IEEEScraper
from scrapers.springer import SpringerScraper
from config import OUTPUT_DIR, PAPER_COLUMNS


# mapping label → scraper class
SOURCES = {
    "acm":      ACMScraper,
    "ieee":     IEEEScraper,
    "springer": SpringerScraper,
}


def run_collection(
    search_strings: Dict[str, str],
    year_start: int,
    year_end: int,
    sources: List[str] | None = None,
    output_dir: str = OUTPUT_DIR,
) -> str:
    """Run paper collection and return the path to the combined xlsx.

    Parameters
    ----------
    search_strings : dict   {label: boolean_query}
    year_start, year_end : int
    sources : list of "acm" / "ieee" / "springer" (None = all)
    output_dir : path for output files

    Returns
    -------
    str  – path to all_collected.xlsx
    """
    if sources is None:
        sources = list(SOURCES.keys())

    os.makedirs(output_dir, exist_ok=True)
    all_frames: List[pd.DataFrame] = []

    driver = setup_driver()
    print(f"\n[COLLECT] Browser opened.  Sources: {', '.join(sources)}")
    print(f"[COLLECT] Years: {year_start}–{year_end}")
    print(f"[COLLECT] Search strings: {len(search_strings)}\n")

    try:
        for src in sources:
            cls = SOURCES.get(src)
            if cls is None:
                print(f"[COLLECT] Unknown source '{src}' — skipping")
                continue

            print(f"\n{'#' * 70}")
            print(f"  COLLECTING FROM: {src.upper()}")
            print(f"{'#' * 70}")

            scraper = cls(driver)
            df = scraper.scrape(search_strings, year_start, year_end)

            if df.empty:
                print(f"[COLLECT] {src}: 0 papers collected")
                continue

            # normalise columns
            for col in PAPER_COLUMNS:
                if col not in df.columns:
                    df[col] = ""

            # save per-source file
            src_path = os.path.join(output_dir, f"{src}_papers.xlsx")
            df.to_excel(src_path, index=False, engine="openpyxl")
            print(f"\n[COLLECT] Saved {len(df)} papers → {src_path}")
            all_frames.append(df)

    except KeyboardInterrupt:
        print("\n[COLLECT] Interrupted — saving partial results …")
    finally:
        driver.quit()
        print("[COLLECT] Browser closed.")

    # combine
    combined_path = os.path.join(output_dir, "all_collected.xlsx")
    if all_frames:
        combined = pd.concat(all_frames, ignore_index=True)
        combined.to_excel(combined_path, index=False, engine="openpyxl")
        print(f"\n[COLLECT] Combined file: {combined_path}  ({len(combined)} papers)")
    else:
        pd.DataFrame(columns=PAPER_COLUMNS).to_excel(
            combined_path, index=False, engine="openpyxl"
        )
        print("[COLLECT] No papers collected — empty combined file created.")

    return combined_path
