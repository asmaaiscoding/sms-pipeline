"""
empirical-validation/dedup-validation.py
─────────────────────────────────────────
V2. Deduplication Precision / Recall Validation

Procedure:
  1. Load combined detection + remediation papers.
  2. Create 10 samples of 300-500 papers, each containing known duplicates.
     Duplicates are created by:
       a) Including naturally occurring DOI duplicates from the pool.
       b) Injecting synthetic duplicates (exact copies, near-identical titles).
  3. Run the two-pass deduplication (DOI + TF-IDF title) on each sample.
  4. Compute precision, recall, F1.
  5. Sensitivity analysis: vary TF-IDF threshold (0.80…0.95).
  6. Analyze false positives (unique papers wrongly merged) and
     false negatives (true duplicates missed).

Outputs → empirical-validation/deduplication-samples/
"""

from __future__ import annotations

import os
import re
import sys
import json
import random
import unicodedata
from typing import Optional

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

# ── paths ────────────────────────────────────────────────────────────────────
BASE = os.path.dirname(os.path.abspath(__file__))
DETECTION_PATH = os.path.join(BASE, "detection_all_collected_papers.xlsx")
REMEDIATION_PATH = os.path.join(BASE, "remediation_all_collected_papers.xlsx")
SAMPLES_DIR = os.path.join(BASE, "deduplication-samples")
os.makedirs(SAMPLES_DIR, exist_ok=True)

SEED = 42
random.seed(SEED)
np.random.seed(SEED)

# ── dedup helpers (mirroring steps/deduplicate.py) ───────────────────────────

def _norm_doi(doi) -> str:
    if not isinstance(doi, str) or doi.strip() == "":
        return ""
    doi = doi.strip().lower()
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)


def _norm_title(title) -> str:
    if not isinstance(title, str):
        return ""
    title = unicodedata.normalize("NFKD", title).lower().strip()
    title = re.sub(r"[^a-z0-9\s]", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def _completeness(row: pd.Series) -> int:
    return sum(1 for v in row if isinstance(v, str) and v.strip())


def run_dedup(df: pd.DataFrame, threshold: float = 0.92) -> set:
    """
    Run deduplication on a DataFrame. Returns the set of *indices* flagged as duplicates.
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

    # Pass 2: Title TF-IDF cosine
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
                if sim[i_pos, j_pos] >= threshold:
                    loser = j_real if df.at[i_real, "_comp"] >= df.at[j_real, "_comp"] else i_real
                    removed.add(loser)
                    if loser == i_real:
                        break

    return removed


# ── sample creation ──────────────────────────────────────────────────────────

def _perturb_title(title: str) -> str:
    """Create a near-duplicate title by small perturbations."""
    perturbations = [
        lambda t: t.replace(":", " -"),
        lambda t: t.replace("-", "–"),
        lambda t: t + " ",
        lambda t: t.lower().title(),
        lambda t: re.sub(r"\s+", "  ", t),
        lambda t: t.replace("and", "&"),
        lambda t: t.replace("using", "Using"),
    ]
    fn = random.choice(perturbations)
    return fn(title)


def create_sample(pool: pd.DataFrame, sample_id: int,
                  target_size: int = None) -> tuple[pd.DataFrame, set]:
    """
    Create one sample with known duplicates.

    Returns (sample_df, ground_truth_dup_indices).
    ground_truth_dup_indices are the indices in sample_df that are duplicates.
    """
    if target_size is None:
        target_size = random.randint(300, 500)

    # Number of unique papers to start with
    n_unique = int(target_size * 0.70)  # ~70% unique base
    n_dupes = target_size - n_unique     # ~30% will be duplicates

    # Sample unique papers from pool
    base_papers = pool.sample(n=min(n_unique, len(pool)), random_state=SEED + sample_id)
    base_papers = base_papers.reset_index(drop=True)

    # Create duplicates
    dup_rows = []
    dup_indices = set()

    for i in range(n_dupes):
        # Pick a random paper from the base to duplicate
        src_idx = random.randint(0, len(base_papers) - 1)
        src = base_papers.iloc[src_idx].copy()

        dup_type = random.choice(["exact_doi", "near_title", "exact_copy"])

        if dup_type == "exact_doi":
            # Same DOI, possibly different title formatting
            row = src.copy()
            if isinstance(row.get("Title"), str):
                row["Title"] = _perturb_title(row["Title"])
            row["Source"] = random.choice(["ACM", "IEEE", "Springer"])
        elif dup_type == "near_title":
            # No DOI but near-identical title
            row = src.copy()
            row["DOI"] = ""
            if isinstance(row.get("Title"), str):
                row["Title"] = _perturb_title(row["Title"])
        else:  # exact_copy
            row = src.copy()

        dup_rows.append(row)

    # Combine base + duplicates
    if dup_rows:
        dup_df = pd.DataFrame(dup_rows)
        sample = pd.concat([base_papers, dup_df], ignore_index=True)
        # Ground truth: the appended rows (indices n_unique .. n_unique+n_dupes-1) are duplicates
        dup_indices = set(range(n_unique, n_unique + len(dup_rows)))
    else:
        sample = base_papers.copy()

    # Shuffle to mix duplicates in
    shuffle_order = list(sample.index)
    random.shuffle(shuffle_order)
    sample = sample.iloc[shuffle_order].reset_index(drop=True)

    # Remap dup_indices after shuffle
    old_to_new = {old: new for new, old in enumerate(shuffle_order)}
    dup_indices_remapped = {old_to_new[i] for i in dup_indices if i in old_to_new}

    return sample, dup_indices_remapped


# ── metrics ──────────────────────────────────────────────────────────────────

def compute_metrics(ground_truth_dups: set, predicted_dups: set, total: int) -> dict:
    tp = len(ground_truth_dups & predicted_dups)
    fp = len(predicted_dups - ground_truth_dups)
    fn = len(ground_truth_dups - predicted_dups)
    tn = total - tp - fp - fn

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


# ── main validation ─────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("V2. DEDUPLICATION PRECISION / RECALL VALIDATION")
    print("=" * 70)

    NUM_SAMPLES = 10
    THRESHOLDS = [0.80, 0.85, 0.90, 0.92, 0.95]

    # ── Load or create samples ───────────────────────────────────────────────
    samples = []
    all_cached = all(
        os.path.exists(os.path.join(SAMPLES_DIR, f"sample_{i+1:02d}.xlsx"))
        and os.path.exists(os.path.join(SAMPLES_DIR, f"sample_{i+1:02d}_ground_truth.json"))
        for i in range(NUM_SAMPLES)
    )

    if all_cached:
        print("\n  Loading cached samples ...")
        for i in range(NUM_SAMPLES):
            sample_df = pd.read_excel(os.path.join(SAMPLES_DIR, f"sample_{i+1:02d}.xlsx"), engine="openpyxl")
            with open(os.path.join(SAMPLES_DIR, f"sample_{i+1:02d}_ground_truth.json")) as f:
                gt_dups = set(json.load(f))
            samples.append((sample_df, gt_dups))
            print(f"  Sample {i+1:2d}: {len(sample_df)} papers, {len(gt_dups)} known duplicates (cached)")
    else:
        print("\n  Ground-truth files missing — generating samples once ...")
        print("  Loading detection papers ...")
        det_df = pd.read_excel(DETECTION_PATH, engine="openpyxl")
        print(f"    {len(det_df)} detection papers")
        print("  Loading remediation papers ...")
        rem_df = pd.read_excel(REMEDIATION_PATH, engine="openpyxl")
        print(f"    {len(rem_df)} remediation papers")
        pool = pd.concat([det_df, rem_df], ignore_index=True)
        pool = pool[pool["Title"].notna() & (pool["Title"].str.strip() != "")]
        print(f"    Combined pool: {len(pool)} papers")

        for i in range(NUM_SAMPLES):
            target = random.randint(300, 500)
            sample_df, gt_dups = create_sample(pool, sample_id=i, target_size=target)
            sample_df.to_excel(os.path.join(SAMPLES_DIR, f"sample_{i+1:02d}.xlsx"), index=False, engine="openpyxl")
            with open(os.path.join(SAMPLES_DIR, f"sample_{i+1:02d}_ground_truth.json"), "w") as f:
                json.dump(sorted(gt_dups), f)
            samples.append((sample_df, gt_dups))
            print(f"  Sample {i+1:2d}: {len(sample_df)} papers, {len(gt_dups)} known duplicates (created)")
        print("  Samples & ground-truth saved — next runs will skip pool loading.")

    # ── Run deduplication at default threshold (0.92) ────────────────────────
    print(f"\n{'─' * 70}")
    print("DEDUPLICATION RESULTS (threshold = 0.92)")
    print(f"{'─' * 70}")

    default_results = []
    for i, (sample_df, gt_dups) in enumerate(samples):
        pred_dups = run_dedup(sample_df, threshold=0.92)
        metrics = compute_metrics(gt_dups, pred_dups, len(sample_df))
        metrics["sample"] = i + 1
        metrics["total"] = len(sample_df)
        metrics["ground_truth_dups"] = len(gt_dups)
        metrics["predicted_dups"] = len(pred_dups)
        default_results.append(metrics)
        print(f"  Sample {i+1:2d}: P={metrics['precision']:.3f}  R={metrics['recall']:.3f}  F1={metrics['f1']:.3f}"
              f"  (TP={metrics['tp']} FP={metrics['fp']} FN={metrics['fn']})")

    # Averages
    avg_p = np.mean([r["precision"] for r in default_results])
    avg_r = np.mean([r["recall"] for r in default_results])
    avg_f1 = np.mean([r["f1"] for r in default_results])
    print(f"\n  AVERAGE:   P={avg_p:.3f}  R={avg_r:.3f}  F1={avg_f1:.3f}")

    # ── Sensitivity analysis ─────────────────────────────────────────────────
    print(f"\n{'─' * 70}")
    print("SENSITIVITY ANALYSIS")
    print(f"{'─' * 70}")

    sensitivity_results = {t: [] for t in THRESHOLDS}
    for threshold in THRESHOLDS:
        for i, (sample_df, gt_dups) in enumerate(samples):
            pred_dups = run_dedup(sample_df, threshold=threshold)
            metrics = compute_metrics(gt_dups, pred_dups, len(sample_df))
            sensitivity_results[threshold].append(metrics)

        avg_p = np.mean([m["precision"] for m in sensitivity_results[threshold]])
        avg_r = np.mean([m["recall"] for m in sensitivity_results[threshold]])
        avg_f1 = np.mean([m["f1"] for m in sensitivity_results[threshold]])
        print(f"  Threshold {threshold:.2f}: AVG P={avg_p:.3f}  R={avg_r:.3f}  F1={avg_f1:.3f}")

    # ── Precision-Recall curve plot ──────────────────────────────────────────
    avg_precisions = []
    avg_recalls = []
    avg_f1s = []
    std_precisions = []
    std_recalls = []
    std_f1s = []
    for t in THRESHOLDS:
        avg_precisions.append(np.mean([m["precision"] for m in sensitivity_results[t]]))
        avg_recalls.append(np.mean([m["recall"] for m in sensitivity_results[t]]))
        avg_f1s.append(np.mean([m["f1"] for m in sensitivity_results[t]]))
        std_precisions.append(np.std([m["precision"] for m in sensitivity_results[t]]))
        std_recalls.append(np.std([m["recall"] for m in sensitivity_results[t]]))
        std_f1s.append(np.std([m["f1"] for m in sensitivity_results[t]]))

    plt.rcParams.update({
        "font.size": 11,
        "axes.titlesize": 13,
        "axes.labelsize": 12,
        "xtick.labelsize": 10,
        "ytick.labelsize": 10,
        "legend.fontsize": 10,
        "figure.facecolor": "white",
    })

    # ── Figure 1: Per-sample results at default threshold ────────────────────
    fig1, axes1 = plt.subplots(1, 2, figsize=(14, 5))

    sample_ids = list(range(1, NUM_SAMPLES + 1))
    p_vals = [r["precision"] for r in default_results]
    r_vals = [r["recall"] for r in default_results]
    f1_vals = [r["f1"] for r in default_results]

    x = np.arange(NUM_SAMPLES)
    w = 0.25
    axes1[0].bar(x - w, p_vals, w, label="Precision", color="#4C72B0", edgecolor="white", linewidth=0.5)
    axes1[0].bar(x, r_vals, w, label="Recall", color="#55A868", edgecolor="white", linewidth=0.5)
    axes1[0].bar(x + w, f1_vals, w, label="F1", color="#C44E52", edgecolor="white", linewidth=0.5)
    axes1[0].axhline(y=avg_f1s[THRESHOLDS.index(0.92)], color="#C44E52", linestyle="--", alpha=0.5, label=f"Avg F1 = {avg_f1s[THRESHOLDS.index(0.92)]:.3f}")
    axes1[0].set_xlabel("Sample")
    axes1[0].set_ylabel("Score")
    axes1[0].set_title("Per-Sample Metrics (threshold = 0.92)")
    axes1[0].set_xticks(x)
    axes1[0].set_xticklabels(sample_ids)
    axes1[0].set_ylim(0, 1.05)
    axes1[0].legend(loc="lower right", framealpha=0.9)
    axes1[0].grid(True, alpha=0.2, axis="y")

    # Confusion counts per sample
    tp_vals = [r["tp"] for r in default_results]
    fp_vals = [r["fp"] for r in default_results]
    fn_vals = [r["fn"] for r in default_results]
    axes1[1].bar(x - w, tp_vals, w, label="TP", color="#55A868", edgecolor="white", linewidth=0.5)
    axes1[1].bar(x, fp_vals, w, label="FP", color="#DD8452", edgecolor="white", linewidth=0.5)
    axes1[1].bar(x + w, fn_vals, w, label="FN", color="#8172B3", edgecolor="white", linewidth=0.5)
    axes1[1].set_xlabel("Sample")
    axes1[1].set_ylabel("Count")
    axes1[1].set_title("TP / FP / FN per Sample (threshold = 0.92)")
    axes1[1].set_xticks(x)
    axes1[1].set_xticklabels(sample_ids)
    axes1[1].legend(loc="upper right", framealpha=0.9)
    axes1[1].grid(True, alpha=0.2, axis="y")

    fig1.tight_layout(pad=2.0)
    fig1.savefig(os.path.join(SAMPLES_DIR, "per_sample_results.png"), dpi=150, bbox_inches="tight")
    plt.close(fig1)
    print(f"\n  Per-sample plot saved -> per_sample_results.png")

    # ── Figure 2: Sensitivity analysis ───────────────────────────────────────
    fig2, axes2 = plt.subplots(1, 3, figsize=(18, 5.5))

    # Plot 1: Metrics vs threshold with error bands
    axes2[0].errorbar(THRESHOLDS, avg_precisions, yerr=std_precisions, fmt="s-",
                      label="Precision", color="#4C72B0", capsize=4, markersize=7, linewidth=2)
    axes2[0].errorbar(THRESHOLDS, avg_recalls, yerr=std_recalls, fmt="o-",
                      label="Recall", color="#55A868", capsize=4, markersize=7, linewidth=2)
    axes2[0].errorbar(THRESHOLDS, avg_f1s, yerr=std_f1s, fmt="^-",
                      label="F1", color="#C44E52", capsize=4, markersize=7, linewidth=2)
    axes2[0].set_xlabel("TF-IDF Threshold")
    axes2[0].set_ylabel("Score (mean ± std)")
    axes2[0].set_title("Metrics vs. TF-IDF Threshold")
    axes2[0].legend(framealpha=0.9)
    axes2[0].set_ylim(0, 1.05)
    axes2[0].grid(True, alpha=0.2)

    # Plot 2: FP / FN bar chart
    avg_fps = []
    avg_fns = []
    for t in THRESHOLDS:
        avg_fps.append(np.mean([m["fp"] for m in sensitivity_results[t]]))
        avg_fns.append(np.mean([m["fn"] for m in sensitivity_results[t]]))
    x_pos = np.arange(len(THRESHOLDS))
    width = 0.35
    bars_fp = axes2[1].bar(x_pos - width / 2, avg_fps, width, label="False Positives",
                           color="#DD8452", edgecolor="white", linewidth=0.5)
    bars_fn = axes2[1].bar(x_pos + width / 2, avg_fns, width, label="False Negatives",
                           color="#8172B3", edgecolor="white", linewidth=0.5)
    for bar in bars_fp:
        axes2[1].text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                      f"{bar.get_height():.1f}", ha="center", va="bottom", fontsize=9)
    for bar in bars_fn:
        axes2[1].text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
                      f"{bar.get_height():.1f}", ha="center", va="bottom", fontsize=9)
    axes2[1].set_xlabel("TF-IDF Threshold")
    axes2[1].set_ylabel("Average Count")
    axes2[1].set_title("Avg False Positives vs False Negatives")
    axes2[1].set_xticks(x_pos)
    axes2[1].set_xticklabels([f"{t:.2f}" for t in THRESHOLDS])
    axes2[1].legend(framealpha=0.9)
    axes2[1].grid(True, alpha=0.2, axis="y")

    # Plot 3: Box plot of F1 across samples per threshold
    f1_by_threshold = [[m["f1"] for m in sensitivity_results[t]] for t in THRESHOLDS]
    bp = axes2[2].boxplot(f1_by_threshold, tick_labels=[f"{t:.2f}" for t in THRESHOLDS],
                          patch_artist=True, widths=0.5)
    colors = ["#4C72B0", "#55A868", "#DD8452", "#C44E52", "#8172B3"]
    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    axes2[2].set_xlabel("TF-IDF Threshold")
    axes2[2].set_ylabel("F1 Score")
    axes2[2].set_title("F1 Distribution Across Samples")
    axes2[2].set_ylim(0, 1.05)
    axes2[2].grid(True, alpha=0.2, axis="y")

    fig2.tight_layout(pad=2.0)
    plot_path = os.path.join(SAMPLES_DIR, "sensitivity_analysis.png")
    fig2.savefig(plot_path, dpi=150, bbox_inches="tight")
    plt.close(fig2)
    print(f"  Sensitivity plot saved -> sensitivity_analysis.png")

    # ── False positive / false negative detailed analysis ────────────────────
    print(f"\n{'─' * 70}")
    print("FALSE POSITIVE / FALSE NEGATIVE ANALYSIS (threshold = 0.92)")
    print(f"{'─' * 70}")

    all_fp_examples = []
    all_fn_examples = []
    for i, (sample_df, gt_dups) in enumerate(samples):
        pred_dups = run_dedup(sample_df, threshold=0.92)
        fp_indices = pred_dups - gt_dups
        fn_indices = gt_dups - pred_dups

        for idx in list(fp_indices)[:3]:  # up to 3 examples per sample
            title = sample_df.at[idx, "Title"] if idx in sample_df.index else "?"
            doi = sample_df.at[idx, "DOI"] if idx in sample_df.index else "?"
            all_fp_examples.append({"sample": i + 1, "index": idx, "title": str(title)[:100], "doi": str(doi)})
        for idx in list(fn_indices)[:3]:
            title = sample_df.at[idx, "Title"] if idx in sample_df.index else "?"
            doi = sample_df.at[idx, "DOI"] if idx in sample_df.index else "?"
            all_fn_examples.append({"sample": i + 1, "index": idx, "title": str(title)[:100], "doi": str(doi)})

    print(f"\n  Total FP examples collected: {len(all_fp_examples)}")
    for ex in all_fp_examples[:10]:
        print(f"    Sample {ex['sample']}: '{ex['title']}' (DOI: {ex['doi']})")

    print(f"\n  Total FN examples collected: {len(all_fn_examples)}")
    for ex in all_fn_examples[:10]:
        print(f"    Sample {ex['sample']}: '{ex['title']}' (DOI: {ex['doi']})")

    # ── Save comprehensive results ───────────────────────────────────────────
    results_summary = {
        "default_threshold": 0.92,
        "num_samples": NUM_SAMPLES,
        "default_results": default_results,
        "sensitivity": {},
        "fp_examples": all_fp_examples[:20],
        "fn_examples": all_fn_examples[:20],
    }
    for t in THRESHOLDS:
        mets = sensitivity_results[t]
        results_summary["sensitivity"][str(t)] = {
            "avg_precision": round(np.mean([m["precision"] for m in mets]), 4),
            "avg_recall": round(np.mean([m["recall"] for m in mets]), 4),
            "avg_f1": round(np.mean([m["f1"] for m in mets]), 4),
            "avg_fp": round(np.mean([m["fp"] for m in mets]), 2),
            "avg_fn": round(np.mean([m["fn"] for m in mets]), 2),
        }

    results_path = os.path.join(SAMPLES_DIR, "validation_results.json")
    with open(results_path, "w") as f:
        json.dump(results_summary, f, indent=2)
    print(f"\n  Results saved -> {results_path}")

    # Save results table as xlsx too
    rows = []
    for t in THRESHOLDS:
        for i, m in enumerate(sensitivity_results[t]):
            rows.append({
                "Threshold": t,
                "Sample": i + 1,
                "Precision": m["precision"],
                "Recall": m["recall"],
                "F1": m["f1"],
                "TP": m["tp"],
                "FP": m["fp"],
                "FN": m["fn"],
                "TN": m["tn"],
            })
    results_df = pd.DataFrame(rows)
    results_xlsx = os.path.join(SAMPLES_DIR, "validation_results.xlsx")
    results_df.to_excel(results_xlsx, index=False, engine="openpyxl")
    print(f"  Results table saved -> {results_xlsx}")

    print(f"\n{'=' * 70}")
    print("V2 VALIDATION COMPLETE")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
