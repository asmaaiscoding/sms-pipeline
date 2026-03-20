"""
V2 Deduplication Empirical Validation
=======================================
Runs the deduplication algorithm on labelled sample files, computes
precision / recall / F1, sensitivity analysis across TF-IDF thresholds,
and produces visualisations + a detailed iteration log.

Ground truth is established via normalised DOI: papers that share the
same normalised DOI are true duplicates.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# ── paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
SAMPLES_DIR = BASE_DIR / "samples"
VIZ_DIR = BASE_DIR / "visualizations"
REPORT_DIR = BASE_DIR / "report"
VIZ_DIR.mkdir(exist_ok=True)
REPORT_DIR.mkdir(exist_ok=True)

SAMPLE_FILES = sorted(SAMPLES_DIR.glob("*.xlsx"))

# ── Normalisation helpers (mirroring backend) ─────────────────────────────────
def _norm_doi(doi) -> str:
    if not doi or (isinstance(doi, float) and np.isnan(doi)):
        return ""
    doi = str(doi).strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:\s*", "", doi)
    return doi


def _norm_title(title) -> str:
    if not title or (isinstance(title, float) and np.isnan(title)):
        return ""
    title = str(title)
    title = unicodedata.normalize("NFKD", title).lower().strip()
    title = title.replace("&", "and")
    title = re.sub(r"[^a-z0-9\s]", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def _completeness(row: pd.Series) -> int:
    count = 0
    for col in ("Title", "Authors", "Year", "Publisher", "DOI", "Link", "Publication"):
        val = row.get(col)
        if val is not None and not (isinstance(val, float) and np.isnan(val)) and str(val).strip():
            count += 1
    return count


# ── Ground-truth labelling ────────────────────────────────────────────────────
def label_ground_truth(df: pd.DataFrame) -> list[int]:
    """
    Return a list of group IDs (one per row). Papers sharing the same group
    are true duplicates.  The canonical grouping key is the normalised DOI.
    Papers without a DOI get a unique singleton group.
    """
    doi_to_group: dict[str, int] = {}
    groups: list[int] = []
    next_id = 0

    for _, row in df.iterrows():
        ndoi = _norm_doi(row.get("DOI"))
        if ndoi:
            if ndoi not in doi_to_group:
                doi_to_group[ndoi] = next_id
                next_id += 1
            groups.append(doi_to_group[ndoi])
        else:
            groups.append(next_id)
            next_id += 1
    return groups


def ground_truth_pairs(groups: list[int]) -> set[tuple[int, int]]:
    """Return set of ordered (i, j) index pairs that are true duplicates."""
    from collections import defaultdict
    gmap = defaultdict(list)
    for idx, g in enumerate(groups):
        gmap[g].append(idx)
    pairs: set[tuple[int, int]] = set()
    for members in gmap.values():
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                pairs.add((members[a], members[b]))
    return pairs


# ── Deduplication algorithm (standalone, mirrors backend) ─────────────────────
def run_dedup(df: pd.DataFrame, tfidf_threshold: float = 0.85) -> tuple[list[bool], list[int | None]]:
    """
    Returns (is_dup, dup_of) lists (len == len(df)).
    is_dup[i] = True means paper i is flagged as duplicate.
    dup_of[i] = index of the paper it is a duplicate of (or None).
    Three-pass: DOI → normalised title → TF-IDF cosine.
    """
    n = len(df)
    is_dup = [False] * n
    dup_of: list[int | None] = [None] * n

    # -- Pass 1: DOI exact match ------------------------------------------------
    doi_groups: dict[str, list[int]] = defaultdict(list)
    for idx, row in df.iterrows():
        ndoi = _norm_doi(row.get("DOI"))
        if ndoi:
            doi_groups[ndoi].append(idx)

    for ndoi, idxs in doi_groups.items():
        if len(idxs) <= 1:
            continue
        comps = [(_completeness(df.iloc[i]), i) for i in idxs]
        best = max(comps, key=lambda x: x[0])[1]
        for i in idxs:
            if i != best and not is_dup[i]:
                is_dup[i] = True
                dup_of[i] = best

    # -- Pass 2: Normalised exact title -----------------------------------------
    # Helper: check if two papers have conflicting (different non-empty) DOIs
    def _dois_conflict(idx_a: int, idx_b: int) -> bool:
        da = _norm_doi(df.iloc[idx_a].get("DOI"))
        db = _norm_doi(df.iloc[idx_b].get("DOI"))
        return bool(da and db and da != db)

    remaining = [i for i in range(n) if not is_dup[i]]
    title_groups: dict[str, list[int]] = defaultdict(list)
    for i in remaining:
        nt = _norm_title(df.iloc[i].get("Title"))
        if nt:
            title_groups[nt].append(i)

    for nt, idxs in title_groups.items():
        if len(idxs) <= 1:
            continue
        comps = [(_completeness(df.iloc[i]), i) for i in idxs]
        best = max(comps, key=lambda x: x[0])[1]
        for i in idxs:
            if i != best and not is_dup[i]:
                if _dois_conflict(i, best):
                    continue  # different DOIs ⇒ different papers
                is_dup[i] = True
                dup_of[i] = best

    # -- Pass 3: TF-IDF cosine similarity --------------------------------------
    remaining = [i for i in range(n) if not is_dup[i]]
    if len(remaining) >= 2:
        titles = [_norm_title(df.iloc[i].get("Title")) for i in remaining]
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 4))
        tfidf = vec.fit_transform(titles)
        sim = cosine_similarity(tfidf)
        np.fill_diagonal(sim, 0)

        for a_pos in range(len(remaining)):
            i = remaining[a_pos]
            if is_dup[i]:
                continue
            for b_pos in range(a_pos + 1, len(remaining)):
                j = remaining[b_pos]
                if is_dup[j]:
                    continue
                if sim[a_pos, b_pos] >= tfidf_threshold:
                    if _dois_conflict(i, j):
                        continue  # different DOIs ⇒ different papers
                    ci = _completeness(df.iloc[i])
                    cj = _completeness(df.iloc[j])
                    if ci >= cj:
                        loser, winner = j, i
                    else:
                        loser, winner = i, j
                    is_dup[loser] = True
                    dup_of[loser] = winner
                    if loser == i:
                        break

    return is_dup, dup_of


# ── Build predicted pairs from dup_of mapping ────────────────────────────────
def _predicted_pairs(n: int, dup_of: list[int | None]) -> set[tuple[int, int]]:
    """Union-find on dup_of to build all predicted duplicate pairs."""
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        if dup_of[i] is not None:
            union(i, dup_of[i])

    groups_map: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups_map[find(i)].append(i)

    pairs: set[tuple[int, int]] = set()
    for members in groups_map.values():
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                pairs.add((members[a], members[b]))
    return pairs


# ── Metrics computation ──────────────────────────────────────────────────────
@dataclass
class DeduplicationMetrics:
    sample_name: str
    total_papers: int
    true_duplicate_pairs: int
    detected_duplicate_count: int
    true_positive: int
    false_positive: int
    false_negative: int
    precision: float
    recall: float
    f1: float
    threshold: float = 0.85

    def to_dict(self) -> dict:
        return asdict(self)


def compute_metrics(
    df: pd.DataFrame,
    is_dup: list[bool],
    groups: list[int],
    sample_name: str,
    threshold: float = 0.85,
) -> DeduplicationMetrics:
    """Compute pair-level precision, recall, F1."""
    gt_pairs = ground_truth_pairs(groups)

    # Re-run to get dup_of mapping (needed for pair construction)
    _, dup_of = run_dedup(df, tfidf_threshold=threshold)
    pred_pairs = _predicted_pairs(len(df), dup_of)

    tp = len(gt_pairs & pred_pairs)
    fp = len(pred_pairs - gt_pairs)
    fn = len(gt_pairs - pred_pairs)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return DeduplicationMetrics(
        sample_name=sample_name,
        total_papers=len(df),
        true_duplicate_pairs=len(gt_pairs),
        detected_duplicate_count=sum(is_dup),
        true_positive=tp,
        false_positive=fp,
        false_negative=fn,
        precision=precision,
        recall=recall,
        f1=f1,
        threshold=threshold,
    )


# ── Error analysis ───────────────────────────────────────────────────────────
def analyse_errors(
    df: pd.DataFrame,
    groups: list[int],
    threshold: float = 0.85,
) -> dict[str, list[dict]]:
    """Return FP and FN cases with paper details for qualitative analysis."""
    gt_pairs = ground_truth_pairs(groups)

    _, dup_of = run_dedup(df, tfidf_threshold=threshold)
    pred_pairs = _predicted_pairs(len(df), dup_of)

    fp_pairs = pred_pairs - gt_pairs
    fn_pairs = gt_pairs - pred_pairs

    def pair_info(i, j):
        ri, rj = df.iloc[i], df.iloc[j]
        return {
            "idx_a": int(i), "idx_b": int(j),
            "title_a": str(ri.get("Title", ""))[:120],
            "title_b": str(rj.get("Title", ""))[:120],
            "doi_a": str(ri.get("DOI", "")),
            "doi_b": str(rj.get("DOI", "")),
            "year_a": str(ri.get("Year", "")),
            "year_b": str(rj.get("Year", "")),
            "source_a": str(ri.get("Source", "")),
            "source_b": str(rj.get("Source", "")),
        }

    fps = [pair_info(i, j) for i, j in sorted(fp_pairs)[:50]]
    fns = [pair_info(i, j) for i, j in sorted(fn_pairs)[:50]]

    return {"false_positives": fps, "false_negatives": fns,
            "fp_count": len(fp_pairs), "fn_count": len(fn_pairs)}


# ── Sensitivity analysis ─────────────────────────────────────────────────────
def sensitivity_analysis(
    df: pd.DataFrame,
    groups: list[int],
    sample_name: str,
    thresholds: list[float] | None = None,
) -> list[DeduplicationMetrics]:
    if thresholds is None:
        thresholds = [0.70, 0.75, 0.80, 0.85, 0.90, 0.92, 0.95, 0.98]
    results = []
    for t in thresholds:
        is_dup, _ = run_dedup(df, tfidf_threshold=t)
        m = compute_metrics(df, is_dup, groups, sample_name, threshold=t)
        results.append(m)
    return results


# ── Main validation runner ────────────────────────────────────────────────────
def run_full_validation(
    tfidf_threshold: float = 0.85,
    iteration_tag: str = "iter1",
) -> dict[str, Any]:
    all_metrics: list[dict] = []
    all_errors: dict[str, dict] = {}
    sensitivity_data: dict[str, list[dict]] = {}
    aggregate_tp = aggregate_fp = aggregate_fn = 0

    print(f"\n{'='*70}")
    print(f"  DEDUPLICATION VALIDATION — {iteration_tag}  (threshold={tfidf_threshold})")
    print(f"{'='*70}\n")

    for fpath in SAMPLE_FILES:
        sample_name = fpath.stem
        print(f"▸ Processing {sample_name} …", end=" ")
        df = pd.read_excel(fpath)
        # Drop fully-empty columns
        df = df.loc[:, df.columns.notna()]
        df = df.dropna(how="all", axis=1)

        groups = label_ground_truth(df)
        is_dup, _ = run_dedup(df, tfidf_threshold=tfidf_threshold)
        m = compute_metrics(df, is_dup, groups, sample_name, threshold=tfidf_threshold)
        all_metrics.append(m.to_dict())
        aggregate_tp += m.true_positive
        aggregate_fp += m.false_positive
        aggregate_fn += m.false_negative

        print(f"P={m.precision:.4f}  R={m.recall:.4f}  F1={m.f1:.4f}  "
              f"(TP={m.true_positive} FP={m.false_positive} FN={m.false_negative})")

        # Error analysis
        errors = analyse_errors(df, groups, threshold=tfidf_threshold)
        all_errors[sample_name] = errors

        # Sensitivity analysis
        sens = sensitivity_analysis(df, groups, sample_name)
        sensitivity_data[sample_name] = [s.to_dict() for s in sens]

    # Aggregate metrics
    agg_p = aggregate_tp / (aggregate_tp + aggregate_fp) if (aggregate_tp + aggregate_fp) else 1.0
    agg_r = aggregate_tp / (aggregate_tp + aggregate_fn) if (aggregate_tp + aggregate_fn) else 1.0
    agg_f1 = 2 * agg_p * agg_r / (agg_p + agg_r) if (agg_p + agg_r) else 0.0

    print(f"\n{'─'*70}")
    print(f"  AGGREGATE — Precision={agg_p:.4f}  Recall={agg_r:.4f}  F1={agg_f1:.4f}")
    print(f"  TP={aggregate_tp}  FP={aggregate_fp}  FN={aggregate_fn}")
    print(f"{'─'*70}\n")

    result = {
        "iteration": iteration_tag,
        "tfidf_threshold": tfidf_threshold,
        "per_sample": all_metrics,
        "aggregate": {
            "precision": agg_p,
            "recall": agg_r,
            "f1": agg_f1,
            "tp": aggregate_tp,
            "fp": aggregate_fp,
            "fn": aggregate_fn,
        },
        "errors": {k: {"fp_count": v["fp_count"], "fn_count": v["fn_count"],
                        "false_positives": v["false_positives"][:10],
                        "false_negatives": v["false_negatives"][:10]}
                   for k, v in all_errors.items()},
        "sensitivity": sensitivity_data,
    }

    # Persist results
    out_path = REPORT_DIR / f"results_{iteration_tag}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"Results saved → {out_path}")

    return result


if __name__ == "__main__":
    tag = sys.argv[1] if len(sys.argv) > 1 else "iter1"
    thresh = float(sys.argv[2]) if len(sys.argv) > 2 else 0.85
    run_full_validation(tfidf_threshold=thresh, iteration_tag=tag)
