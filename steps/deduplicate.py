"""
steps/deduplicate.py
--------------------
Step 2 — Remove duplicate papers from an xlsx file.

Strategy (by priority):
  1. Identical normalised DOI   → keep the row with the most fields filled
  2. Near-identical title (TF-IDF cosine ≥ threshold) → same rule

Usage (stand-alone):
    python -m steps.deduplicate input.xlsx output.xlsx
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from typing import Optional

import numpy as np
import pandas as pd

from config import TITLE_SIM_THRESHOLD, OUTPUT_DIR

# Lazy import — sklearn is heavy; only load when actually deduplicating
TfidfVectorizer = None
cosine_similarity = None


def _ensure_sklearn():
    global TfidfVectorizer, cosine_similarity
    if TfidfVectorizer is None:
        from sklearn.feature_extraction.text import TfidfVectorizer as _T
        from sklearn.metrics.pairwise import cosine_similarity as _C
        TfidfVectorizer = _T
        cosine_similarity = _C


# ═══════════════════════════════════════════════════════════════════════════════
#  Normalisation helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _norm_doi(doi) -> str:
    if not isinstance(doi, str) or doi.strip() == "":
        return ""
    doi = doi.strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    return doi


def _norm_title(title) -> str:
    if not isinstance(title, str):
        return ""
    title = unicodedata.normalize("NFKD", title).lower().strip()
    title = re.sub(r"[^a-z0-9\s]", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def _completeness(row: pd.Series) -> int:
    """Count the number of non-empty string cells in *row*."""
    return sum(1 for v in row if isinstance(v, str) and v.strip())


# ═══════════════════════════════════════════════════════════════════════════════
#  Core deduplication
# ═══════════════════════════════════════════════════════════════════════════════

def deduplicate(df: pd.DataFrame, threshold: float = TITLE_SIM_THRESHOLD) -> pd.DataFrame:
    """Return a copy of *df* with duplicates removed."""
    df = df.copy()
    df["_doi"]  = df["DOI"].apply(_norm_doi)   if "DOI"   in df.columns else ""
    df["_title"] = df["Title"].apply(_norm_title) if "Title" in df.columns else ""
    df["_comp"] = df.apply(_completeness, axis=1)

    removed: set = set()

    # ── 1. DOI-based ──────────────────────────────────────────────────────────
    doi_groups: dict = {}
    for idx, doi in df["_doi"].items():
        if doi:
            doi_groups.setdefault(doi, []).append(idx)

    doi_removed = set()
    for indices in doi_groups.values():
        if len(indices) > 1:
            best = max(indices, key=lambda i: df.at[i, "_comp"])
            doi_removed.update(i for i in indices if i != best)
    removed |= doi_removed
    print(f"[DEDUP] Removed by DOI:   {len(doi_removed)}")

    # ── 2. Title-similarity ───────────────────────────────────────────────────
    remaining = [i for i in df.index if i not in removed]
    titles = df.loc[remaining, "_title"].tolist()

    title_removed: set = set()
    if len(titles) >= 2:
        _ensure_sklearn()
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 4))
        tfidf = vec.fit_transform(titles)
        sim = cosine_similarity(tfidf)
        np.fill_diagonal(sim, 0)

        for i_pos, i_real in enumerate(remaining):
            if i_real in title_removed:
                continue
            for j_pos in range(i_pos + 1, len(remaining)):
                j_real = remaining[j_pos]
                if j_real in title_removed:
                    continue
                if sim[i_pos, j_pos] >= threshold:
                    loser = j_real if df.at[i_real, "_comp"] >= df.at[j_real, "_comp"] else i_real
                    title_removed.add(loser)
                    if loser == i_real:
                        break

    removed |= title_removed
    print(f"[DEDUP] Removed by title: {len(title_removed)}")

    final = (
        df.drop(index=list(removed))
          .drop(columns=["_doi", "_title", "_comp"])
          .reset_index(drop=True)
    )
    return final


# ═══════════════════════════════════════════════════════════════════════════════
#  Pipeline entry point
# ═══════════════════════════════════════════════════════════════════════════════

def run_deduplication(
    input_path: str,
    output_path: Optional[str] = None,
) -> str:
    """Deduplicate *input_path* and write to *output_path*.  Returns output path."""
    if output_path is None:
        output_path = os.path.join(OUTPUT_DIR, "deduplicated.xlsx")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    print(f"\n[DEDUP] Reading {input_path} …")
    df = pd.read_excel(input_path)
    print(f"[DEDUP] {len(df)} rows in")

    df_clean = deduplicate(df)
    df_clean.to_excel(output_path, index=False, engine="openpyxl")
    print(f"[DEDUP] {len(df_clean)} rows out → {output_path}")

    return output_path


# ── stand-alone ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("Usage: python -m steps.deduplicate input.xlsx output.xlsx")
    run_deduplication(sys.argv[1], sys.argv[2])
