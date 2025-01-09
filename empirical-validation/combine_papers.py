"""
Combine per-source collected papers into single files for empirical validation.
- Detection: uses existing *_all_papers.xlsx files
- Remediation: combines per-year files (no all_papers equivalent exists)
"""

import os
import glob
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
OUT_DIR = BASE

STANDARD_COLS = ["Title", "Year", "Authors", "Publisher", "Source", "DOI", "Link", "Search_String"]


def _read_and_standardize(path: str) -> pd.DataFrame:
    df = pd.read_excel(path, engine="openpyxl")
    # Rename 'Publication' -> 'Authors' if needed (IEEE has Publication column)
    if "Publication" in df.columns and "Authors" not in df.columns:
        df.rename(columns={"Publication": "Authors"}, inplace=True)
    # Drop extra columns like 'Period'
    for col in STANDARD_COLS:
        if col not in df.columns:
            df[col] = ""
    return df[STANDARD_COLS]


def combine_detection():
    print("=== Combining Detection files ===")
    files = [
        os.path.join(ROOT, "output", "collection", "acm", "detection", "acm_all_papers.xlsx"),
        os.path.join(ROOT, "output", "collection", "ieee", "detection", "ieee_all_papers.xlsx"),
        os.path.join(ROOT, "output", "collection", "springer", "detection", "springer_all_papers.xlsx"),
    ]
    dfs = []
    for f in files:
        print(f"  Reading {os.path.basename(f)} ...")
        df = _read_and_standardize(f)
        print(f"    -> {len(df)} rows")
        dfs.append(df)

    combined = pd.concat(dfs, ignore_index=True)
    out_path = os.path.join(OUT_DIR, "detection_all_collected_papers.xlsx")
    print(f"  Writing {len(combined)} total rows -> {out_path}")
    combined.to_excel(out_path, index=False, engine="openpyxl")
    print("  Done!")
    return combined


def combine_remediation():
    print("\n=== Combining Remediation files ===")
    # No all_papers.xlsx for remediation — use per-year files from each source
    source_dirs = {
        "acm": os.path.join(ROOT, "output", "collection", "acm", "remediation", "acm-per-year"),
        "ieee": os.path.join(ROOT, "output", "collection", "ieee", "remediation", "ieee_by_year"),
        "springer": os.path.join(ROOT, "output", "collection", "springer", "remediation", "springer_by_year"),
    }
    dfs = []
    for source, dirpath in source_dirs.items():
        files = sorted(glob.glob(os.path.join(dirpath, "*.xlsx")))
        print(f"  {source}: {len(files)} per-year files")
        source_dfs = []
        for f in files:
            try:
                df = _read_and_standardize(f)
                source_dfs.append(df)
            except Exception as e:
                print(f"    Warning: could not read {os.path.basename(f)}: {e}")
        if source_dfs:
            source_combined = pd.concat(source_dfs, ignore_index=True)
            print(f"    -> {len(source_combined)} total rows for {source}")
            dfs.append(source_combined)

    combined = pd.concat(dfs, ignore_index=True)
    out_path = os.path.join(OUT_DIR, "remediation_all_collected_papers.xlsx")
    print(f"  Writing {len(combined)} total rows -> {out_path}")
    combined.to_excel(out_path, index=False, engine="openpyxl")
    print("  Done!")
    return combined


if __name__ == "__main__":
    combine_detection()
    combine_remediation()
    print("\nAll files combined successfully!")

