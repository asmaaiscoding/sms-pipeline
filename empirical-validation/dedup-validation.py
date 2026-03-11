"""
empirical-validation/dedup-validation.py

V4. Deduplication Precision / Recall Validation

Procedure:
  1. Load pre-existing samples from deduplication-samples/files/.
     Each sample has a Ground_Truth column (DUPLICATE / UNIQUE).
  2. Run 3-pass deduplication (DOI + exact title + TF-IDF cosine).
  3. Compute GROUP-AWARE precision, recall, F1.
     A ground-truth duplicate is "detected" if ANY member of its
     duplicate group was predicted for removal (fixes wrong-survivor bias).
  4. Analyze false positives and false negatives.
  5. Generate interpretation report.

Outputs -> empirical-validation/deduplication-samples/resuls/
"""

from __future__ import annotations

import os
import re
import json
import unicodedata

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# -- paths -----------------------------------------------------------------
BASE = os.path.dirname(os.path.abspath(__file__))
FILES_DIR = os.path.join(BASE, "deduplication-samples", "files")
RESULTS_DIR = os.path.join(BASE, "deduplication-samples", "resuls")
os.makedirs(RESULTS_DIR, exist_ok=True)

NUM_SAMPLES = 10

# -- normalisation helpers (mirrors steps/deduplicate.py) ------------------

def _norm_doi(doi) -> str:
    if not isinstance(doi, str) or doi.strip() == "":
        return ""
    doi = doi.strip().lower()
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)


def _norm_title(title) -> str:
    if not isinstance(title, str):
        return ""
    title = unicodedata.normalize("NFKD", title).lower().strip()
    title = title.replace("&", "and")
    title = re.sub(r"[^a-z0-9\s]", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def _completeness(row: pd.Series) -> int:
    return sum(1 for v in row if isinstance(v, str) and v.strip())


# -- 3-pass dedup ----------------------------------------------------------

def run_dedup(df: pd.DataFrame) -> set:
    """
    Run 3-pass deduplication: DOI + exact title + TF-IDF cosine.
    Returns the set of *indices* flagged for removal.
    """
    df = df.copy()
    df["_doi"] = df["DOI"].apply(_norm_doi) if "DOI" in df.columns else ""
    df["_title"] = df["Title"].apply(_norm_title) if "Title" in df.columns else ""
    df["_comp"] = df.apply(_completeness, axis=1)

    removed: set = set()

    # Pass 1: DOI
    doi_groups: dict = {}
    for idx, doi in df["_doi"].items():
        if doi:
            doi_groups.setdefault(doi, []).append(idx)
    for indices in doi_groups.values():
        if len(indices) > 1:
            best = max(indices, key=lambda i: df.at[i, "_comp"])
            removed.update(i for i in indices if i != best)

    # Pass 2: exact normalised title
    title_groups: dict = {}
    for idx in df.index:
        if idx in removed:
            continue
        nt = df.at[idx, "_title"]
        if nt:
            title_groups.setdefault(nt, []).append(idx)
    for indices in title_groups.values():
        if len(indices) > 1:
            best = max(indices, key=lambda i: df.at[i, "_comp"])
            for i in indices:
                if i != best and i not in removed:
                    removed.add(i)

    # Pass 3: TF-IDF cosine similarity
    remaining = [i for i in df.index if i not in removed]
    titles = df.loc[remaining, "_title"].tolist()
    if len(titles) >= 2:
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 4))
        tfidf = vec.fit_transform(titles)
        sim = cosine_similarity(tfidf)
        np.fill_diagonal(sim, 0)
        for i_pos, i_real in enumerate(remaining):
            if i_real in removed:
                continue
            for j_pos in range(i_pos + 1, len(remaining)):
                j_real = remaining[j_pos]
                if j_real in removed:
                    continue
                if sim[i_pos, j_pos] >= 0.85:
                    loser = j_real if df.at[i_real, "_comp"] >= df.at[j_real, "_comp"] else i_real
                    removed.add(loser)
                    if loser == i_real:
                        break

    return removed


# -- group-aware metrics ---------------------------------------------------

def _build_groups(df: pd.DataFrame) -> dict[int, set[int]]:
    """
    Build duplicate groups using union-find over DOI + normalised title.
    Returns {root_index: set_of_member_indices}.
    """
    parent = {idx: idx for idx in df.index}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        a, b = find(a), find(b)
        if a != b:
            parent[a] = b

    # DOI unions
    doi_map: dict[str, int] = {}
    for idx in df.index:
        doi = _norm_doi(df.at[idx, "DOI"]) if "DOI" in df.columns else ""
        if doi:
            if doi in doi_map:
                union(idx, doi_map[doi])
            else:
                doi_map[doi] = idx

    # Title unions
    title_map: dict[str, int] = {}
    for idx in df.index:
        nt = _norm_title(df.at[idx, "Title"]) if "Title" in df.columns else ""
        if nt:
            if nt in title_map:
                union(idx, title_map[nt])
            else:
                title_map[nt] = idx

    groups: dict[int, set[int]] = {}
    for idx in df.index:
        root = find(idx)
        groups.setdefault(root, set()).add(idx)
    return groups


def compute_metrics(df: pd.DataFrame, gt_dups: set, pred_removed: set) -> dict:
    """
    Group-aware dedup metrics.

    TP: a ground-truth duplicate whose group has at least one predicted removal
        (the algorithm detected the duplication — which member it removed is irrelevant).
    FN: a ground-truth duplicate whose group has NO predicted removals.
    FP: a predicted removal whose group contains NO ground-truth duplicates at all
        (the algorithm wrongly merged unrelated papers).
    """
    groups = _build_groups(df)

    # map each index → its group set
    idx_to_group: dict[int, set[int]] = {}
    for _root, members in groups.items():
        for idx in members:
            idx_to_group[idx] = members

    tp = fn = 0
    for idx in gt_dups:
        group = idx_to_group.get(idx, {idx})
        if group & pred_removed:
            tp += 1
        else:
            fn += 1

    fp = 0
    for idx in pred_removed:
        group = idx_to_group.get(idx, {idx})
        if not (group & gt_dups):
            fp += 1

    tn = len(df) - tp - fp - fn

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


# -- interpretation report -------------------------------------------------

def write_interpretation(path: str, results: list, fp_examples: list, fn_examples: list):
    avg_p = np.mean([r["precision"] for r in results])
    avg_r = np.mean([r["recall"] for r in results])
    avg_f1 = np.mean([r["f1"] for r in results])

    lines = []
    lines.append("=" * 70)
    lines.append("DEDUPLICATION VALIDATION -- INTERPRETATION OF RESULTS")
    lines.append("=" * 70)

    lines.append("")
    lines.append("1. OVERVIEW")
    lines.append("-" * 40)
    lines.append(f"Number of samples evaluated: {len(results)}")
    lines.append(f"Deduplication method: 3-pass (DOI + exact title + TF-IDF cosine)")
    lines.append(f"Evaluation: group-aware (detects duplication regardless of which member is removed)")
    lines.append("")
    for r in results:
        lines.append(f"  Sample {r['sample']:2d}: {r['total']} papers, "
                     f"{r['ground_truth_dups']} known duplicates  ->  "
                     f"P={r['precision']:.3f}  R={r['recall']:.3f}  F1={r['f1']:.3f}")

    lines.append("")
    lines.append("2. METHOD DESCRIPTION")
    lines.append("-" * 40)
    lines.append("  The deduplication pipeline uses three sequential passes:")
    lines.append("    Pass 1 - DOI exact match: papers sharing the same normalised DOI")
    lines.append("            are grouped; the most complete record is kept.")
    lines.append("    Pass 2 - Exact normalised title: among remaining papers, those with")
    lines.append("            identical normalised titles (NFKD + lowercase + '&'->'and'")
    lines.append("            + strip punctuation) are grouped; the most complete is kept.")
    lines.append("    Pass 3 - TF-IDF cosine similarity: among remaining papers, titles")
    lines.append("            are vectorised with character n-grams (3-4); pairs with")
    lines.append("            cosine similarity >= 0.85 are merged, keeping the more")
    lines.append("            complete record.")
    lines.append("")
    lines.append("  Evaluation uses GROUP-AWARE metrics: a ground-truth duplicate is")
    lines.append("  counted as detected (TP) if any member of its duplicate group was")
    lines.append("  flagged for removal. This eliminates wrong-survivor bias where the")
    lines.append("  algorithm correctly identifies a duplicate pair but removes a")
    lines.append("  different member than the one labelled 'DUPLICATE' in the ground truth.")

    lines.append("")
    lines.append("3. AGGREGATE PERFORMANCE")
    lines.append("-" * 40)
    lines.append(f"  Average Precision : {avg_p:.3f}")
    lines.append(f"  Average Recall    : {avg_r:.3f}")
    lines.append(f"  Average F1        : {avg_f1:.3f}")
    lines.append("")
    if avg_p > 0.95:
        lines.append("  Precision is EXCELLENT -- virtually no unique papers are wrongly merged.")
    elif avg_p > 0.85:
        lines.append("  Precision is HIGH -- few unique papers are being wrongly merged.")
    elif avg_p > 0.65:
        lines.append("  Precision is MODERATE -- some unique papers are being wrongly flagged.")
    else:
        lines.append("  Precision is LOW -- many unique papers are being incorrectly removed.")
    if avg_r > 0.95:
        lines.append("  Recall is EXCELLENT -- virtually all true duplicates are detected.")
    elif avg_r > 0.85:
        lines.append("  Recall is HIGH -- most true duplicates are correctly detected.")
    elif avg_r > 0.65:
        lines.append("  Recall is MODERATE -- a notable fraction of true duplicates are missed.")
    else:
        lines.append("  Recall is LOW -- many true duplicates escape detection.")

    lines.append("")
    lines.append("4. ERROR ANALYSIS")
    lines.append("-" * 40)
    lines.append(f"  Total false positive examples (unique papers wrongly removed): {len(fp_examples)}")
    lines.append(f"  Total false negative examples (duplicates missed): {len(fn_examples)}")
    lines.append("")
    if fp_examples:
        lines.append("  Sample FALSE POSITIVES (unique papers incorrectly flagged as duplicates):")
        for ex in fp_examples[:10]:
            lines.append(f"    Sample {ex['sample']}: {ex['title']}")
    if fn_examples:
        lines.append("")
        lines.append("  Sample FALSE NEGATIVES (true duplicates that were missed):")
        for ex in fn_examples[:10]:
            lines.append(f"    Sample {ex['sample']}: {ex['title']}")

    lines.append("")
    lines.append("5. CONCLUSION")
    lines.append("-" * 40)
    if avg_f1 > 0.95:
        lines.append("  The 3-pass deduplication pipeline achieves EXCELLENT performance.")
        lines.append("  The combination of DOI matching, exact-title matching, and TF-IDF")
        lines.append("  cosine similarity effectively covers all major duplication patterns.")
    elif avg_f1 > 0.85:
        lines.append("  The 3-pass deduplication pipeline performs WELL.")
        lines.append("  Most duplicates are detected across all three passes.")
    elif avg_f1 > 0.65:
        lines.append("  The 3-pass deduplication pipeline has MODERATE performance.")
        lines.append("  Some duplicates are not caught; further tuning may help.")
    else:
        lines.append("  The 3-pass deduplication pipeline has LIMITED effectiveness.")
        lines.append("  Additional heuristics or lower thresholds may be needed.")

    lines.append("")
    lines.append("=" * 70)

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# -- main validation -------------------------------------------------------

def main():
    print("=" * 70)
    print("V4. DEDUPLICATION PRECISION / RECALL VALIDATION (3-pass + group-aware)")
    print("=" * 70)

    # -- Load samples from xlsx --------------------------------------------
    samples: list[tuple[pd.DataFrame, set]] = []
    for i in range(NUM_SAMPLES):
        path = os.path.join(FILES_DIR, f"sample_{i+1:02d}.xlsx")
        if not os.path.exists(path):
            print(f"  WARNING: {path} not found, skipping.")
            continue
        df = pd.read_excel(path, engine="openpyxl")
        if "Ground_Truth" not in df.columns:
            print(f"  WARNING: sample_{i+1:02d}.xlsx has no Ground_Truth column, skipping.")
            continue
        gt_dups = set(df.index[df["Ground_Truth"] == "DUPLICATE"].tolist())
        samples.append((df, gt_dups))
        print(f"  Sample {i+1:2d}: {len(df)} papers, {len(gt_dups)} known duplicates")

    if not samples:
        print("ERROR: No sample files found.")
        return

    # -- Run 3-pass deduplication with group-aware metrics -----------------
    print(f"\n{'=' * 70}")
    print("DEDUPLICATION RESULTS (3-pass + group-aware metrics)")
    print(f"{'=' * 70}")

    results = []
    for i, (sample_df, gt_dups) in enumerate(samples):
        pred_dups = run_dedup(sample_df)
        metrics = compute_metrics(sample_df, gt_dups, pred_dups)
        metrics["sample"] = i + 1
        metrics["total"] = len(sample_df)
        metrics["ground_truth_dups"] = len(gt_dups)
        metrics["predicted_dups"] = len(pred_dups)
        results.append(metrics)
        print(f"  Sample {i+1:2d}: P={metrics['precision']:.3f}  R={metrics['recall']:.3f}  F1={metrics['f1']:.3f}"
              f"  (TP={metrics['tp']} FP={metrics['fp']} FN={metrics['fn']})")

    avg_p = np.mean([r["precision"] for r in results])
    avg_r = np.mean([r["recall"] for r in results])
    avg_f1 = np.mean([r["f1"] for r in results])
    print(f"\n  AVERAGE:   P={avg_p:.3f}  R={avg_r:.3f}  F1={avg_f1:.3f}")

    # -- Plots -------------------------------------------------------------
    plt.rcParams.update({
        "font.size": 11, "axes.titlesize": 13, "axes.labelsize": 12,
        "xtick.labelsize": 10, "ytick.labelsize": 10, "legend.fontsize": 10,
        "figure.facecolor": "white",
    })

    n_samples = len(samples)
    sample_ids = list(range(1, n_samples + 1))
    x = np.arange(n_samples)
    w = 0.25

    # Figure 1: Per-sample metrics + TP/FP/FN
    fig1, axes1 = plt.subplots(1, 2, figsize=(14, 5))

    axes1[0].bar(x - w, [r["precision"] for r in results], w, label="Precision", color="#4C72B0", edgecolor="white", linewidth=0.5)
    axes1[0].bar(x, [r["recall"] for r in results], w, label="Recall", color="#55A868", edgecolor="white", linewidth=0.5)
    axes1[0].bar(x + w, [r["f1"] for r in results], w, label="F1", color="#C44E52", edgecolor="white", linewidth=0.5)
    axes1[0].axhline(y=avg_f1, color="#C44E52", linestyle="--", alpha=0.5, label=f"Avg F1 = {avg_f1:.3f}")
    axes1[0].set_xlabel("Sample")
    axes1[0].set_ylabel("Score")
    axes1[0].set_title("Per-Sample Metrics (3-pass)")
    axes1[0].set_xticks(x)
    axes1[0].set_xticklabels(sample_ids)
    axes1[0].set_ylim(0, 1.05)
    axes1[0].legend(loc="lower right", framealpha=0.9)
    axes1[0].grid(True, alpha=0.2, axis="y")

    axes1[1].bar(x - w, [r["tp"] for r in results], w, label="TP", color="#55A868", edgecolor="white", linewidth=0.5)
    axes1[1].bar(x, [r["fp"] for r in results], w, label="FP", color="#DD8452", edgecolor="white", linewidth=0.5)
    axes1[1].bar(x + w, [r["fn"] for r in results], w, label="FN", color="#8172B3", edgecolor="white", linewidth=0.5)
    axes1[1].set_xlabel("Sample")
    axes1[1].set_ylabel("Count")
    axes1[1].set_title("TP / FP / FN per Sample (3-pass)")
    axes1[1].set_xticks(x)
    axes1[1].set_xticklabels(sample_ids)
    axes1[1].legend(loc="upper right", framealpha=0.9)
    axes1[1].grid(True, alpha=0.2, axis="y")

    fig1.tight_layout(pad=2.0)
    fig1.savefig(os.path.join(RESULTS_DIR, "per_sample_results.png"), dpi=150, bbox_inches="tight")
    plt.close(fig1)
    print(f"\n  Per-sample plot saved -> per_sample_results.png")

    # Figure 2: Box plots + aggregate error breakdown
    fig2, axes2 = plt.subplots(1, 2, figsize=(14, 5))

    bp_data = [
        [r["precision"] for r in results],
        [r["recall"] for r in results],
        [r["f1"] for r in results],
    ]
    bp = axes2[0].boxplot(bp_data, tick_labels=["Precision", "Recall", "F1"],
                          patch_artist=True, widths=0.5)
    bp_colors = ["#4C72B0", "#55A868", "#C44E52"]
    for patch, color in zip(bp["boxes"], bp_colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    axes2[0].set_ylabel("Score")
    axes2[0].set_title("Metric Distributions Across Samples")
    axes2[0].set_ylim(0, 1.05)
    axes2[0].grid(True, alpha=0.2, axis="y")

    total_fp = sum(r["fp"] for r in results)
    total_fn = sum(r["fn"] for r in results)
    total_tp = sum(r["tp"] for r in results)
    bars = axes2[1].bar(["True Positives", "False Positives", "False Negatives"],
                        [total_tp, total_fp, total_fn],
                        color=["#55A868", "#DD8452", "#8172B3"],
                        edgecolor="white", linewidth=0.5)
    for bar in bars:
        h = bar.get_height()
        axes2[1].text(bar.get_x() + bar.get_width() / 2, h + 1,
                      str(int(h)), ha="center", va="bottom", fontsize=11, fontweight="bold")
    axes2[1].set_ylabel("Total Count (all samples)")
    axes2[1].set_title("Aggregate Error Breakdown")
    axes2[1].grid(True, alpha=0.2, axis="y")

    fig2.tight_layout(pad=2.0)
    fig2.savefig(os.path.join(RESULTS_DIR, "error_analysis.png"), dpi=150, bbox_inches="tight")
    plt.close(fig2)
    print(f"  Error analysis plot saved -> error_analysis.png")

    # -- FP / FN detailed analysis -----------------------------------------
    print(f"\n{'=' * 70}")
    print("FALSE POSITIVE / FALSE NEGATIVE ANALYSIS")
    print(f"{'=' * 70}")

    all_fp_examples, all_fn_examples = [], []
    for i, (sample_df, gt_dups) in enumerate(samples):
        pred_dups = run_dedup(sample_df)
        groups = _build_groups(sample_df)
        idx_to_group: dict[int, set[int]] = {}
        for _root, members in groups.items():
            for idx in members:
                idx_to_group[idx] = members

        # FP: predicted removal whose group has no GT dups
        for idx in pred_dups:
            group = idx_to_group.get(idx, {idx})
            if not (group & gt_dups):
                title = sample_df.at[idx, "Title"] if idx in sample_df.index else "?"
                doi = sample_df.at[idx, "DOI"] if idx in sample_df.index else "?"
                all_fp_examples.append({"sample": i + 1, "index": idx, "title": str(title)[:100], "doi": str(doi)})

        # FN: GT dup whose group has no predicted removal
        for idx in gt_dups:
            group = idx_to_group.get(idx, {idx})
            if not (group & pred_dups):
                title = sample_df.at[idx, "Title"] if idx in sample_df.index else "?"
                doi = sample_df.at[idx, "DOI"] if idx in sample_df.index else "?"
                all_fn_examples.append({"sample": i + 1, "index": idx, "title": str(title)[:100], "doi": str(doi)})

    print(f"\n  FP examples: {len(all_fp_examples)}")
    for ex in all_fp_examples[:10]:
        print(f"    Sample {ex['sample']}: '{ex['title']}'")
    print(f"\n  FN examples: {len(all_fn_examples)}")
    for ex in all_fn_examples[:10]:
        print(f"    Sample {ex['sample']}: '{ex['title']}'")

    # -- Save results ------------------------------------------------------
    results_summary = {
        "method": "3-pass (DOI + title + TF-IDF) with group-aware metrics",
        "num_samples": len(samples),
        "results": results,
        "averages": {
            "precision": round(float(avg_p), 4),
            "recall": round(float(avg_r), 4),
            "f1": round(float(avg_f1), 4),
        },
        "fp_examples": all_fp_examples[:20],
        "fn_examples": all_fn_examples[:20],
    }

    with open(os.path.join(RESULTS_DIR, "validation_results.json"), "w") as f:
        json.dump(results_summary, f, indent=2)
    print(f"\n  Results saved -> validation_results.json")

    rows = []
    for r in results:
        rows.append({"Sample": r["sample"], "Precision": r["precision"],
                     "Recall": r["recall"], "F1": r["f1"],
                     "TP": r["tp"], "FP": r["fp"], "FN": r["fn"], "TN": r["tn"]})
    pd.DataFrame(rows).to_excel(os.path.join(RESULTS_DIR, "validation_results.xlsx"), index=False, engine="openpyxl")
    print(f"  Results table saved -> validation_results.xlsx")

    # -- Write interpretation ----------------------------------------------
    interp_path = os.path.join(RESULTS_DIR, "interpretation.txt")
    write_interpretation(interp_path, results, all_fp_examples, all_fn_examples)
    print(f"  Interpretation saved -> interpretation.txt")

    print(f"\n{'=' * 70}")
    print("V4 VALIDATION COMPLETE")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
