"""
Generate visualisations for the V2 Deduplication empirical validation.
Reads results_iter1.json and results_iter2.json, produces publication-quality
charts saved to V2_Deduplication/visualizations/.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

REPORT_DIR = Path(__file__).resolve().parent / "report"
VIZ_DIR = Path(__file__).resolve().parent / "visualizations"
VIZ_DIR.mkdir(exist_ok=True)

# ── Load data ─────────────────────────────────────────────────────────────────
def _load(tag: str) -> dict:
    with open(REPORT_DIR / f"results_{tag}.json") as f:
        return json.load(f)

iter1 = _load("iter1")
iter2 = _load("iter2")

SAMPLE_SHORT = {
    "sample_01_Exact_duplicates": "S01\nExact",
    "sample_02_Title_case_variants": "S02\nCase",
    "sample_03_DOI_format_variants": "S03\nDOI fmt",
    "sample_04_Cross-source_duplicates": "S04\nCross-src",
    "sample_05_Whitespace_punctuation_noise": "S05\nWhitespace",
    "sample_06_Year_off-by-one": "S06\nYear±1",
    "sample_07_PartialTruncated_titles": "S07\nTruncated",
    "sample_08_Triple_Duplicates": "S08\nTriple",
    "sample_09_Publisher_Field_Swapped": "S09\nPub swap",
    "sample_10_mixed": "S10\nMixed",
}

plt.rcParams.update({
    "figure.dpi": 150,
    "font.size": 10,
    "axes.titlesize": 12,
    "axes.labelsize": 11,
    "legend.fontsize": 9,
    "figure.facecolor": "white",
})


# ── 1. Per-sample F1 comparison: Iteration 1 vs 2 ─────────────────────────────
def plot_per_sample_comparison():
    fig, axes = plt.subplots(1, 3, figsize=(16, 5), sharey=True)
    metrics = ["precision", "recall", "f1"]
    titles_map = {"precision": "Precision", "recall": "Recall", "f1": "F1 Score"}

    samples1 = {s["sample_name"]: s for s in iter1["per_sample"]}
    samples2 = {s["sample_name"]: s for s in iter2["per_sample"]}
    names = list(samples1.keys())
    short = [SAMPLE_SHORT.get(n, n) for n in names]
    x = np.arange(len(names))
    w = 0.35

    for ax, metric in zip(axes, metrics):
        v1 = [samples1[n][metric] for n in names]
        v2 = [samples2[n][metric] for n in names]
        bars1 = ax.bar(x - w/2, v1, w, label="Iteration 1 (baseline)", color="#4C72B0", edgecolor="white")
        bars2 = ax.bar(x + w/2, v2, w, label="Iteration 2 (refined)", color="#55A868", edgecolor="white")
        ax.set_title(titles_map[metric], fontweight="bold")
        ax.set_xticks(x)
        ax.set_xticklabels(short, fontsize=7)
        ax.set_ylim(0.97, 1.005)
        ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.3f"))
        ax.axhline(1.0, color="gray", linestyle="--", linewidth=0.7, alpha=0.5)
        if metric == "precision":
            ax.set_ylabel("Score")
        ax.legend(loc="lower left")

    fig.suptitle("Deduplication Metrics: Iteration 1 (Baseline) vs Iteration 2 (Refined)",
                 fontsize=13, fontweight="bold", y=1.02)
    fig.tight_layout()
    fig.savefig(VIZ_DIR / "fig1_per_sample_comparison.png", bbox_inches="tight")
    plt.close(fig)
    print("✓ fig1_per_sample_comparison.png")


# ── 2. Sensitivity analysis: P-R curve across TF-IDF thresholds ──────────────
def plot_sensitivity_curves():
    fig, axes = plt.subplots(2, 5, figsize=(20, 8), sharex=True, sharey=True)
    axes = axes.flatten()

    colors = {"precision": "#4C72B0", "recall": "#DD8452", "f1": "#55A868"}

    # Use iter2 sensitivity data (refined algorithm)
    sens = iter2["sensitivity"]
    for idx, (sample_name, records) in enumerate(sens.items()):
        ax = axes[idx]
        thresholds = [r["threshold"] for r in records]
        for metric, color in colors.items():
            values = [r[metric] for r in records]
            ax.plot(thresholds, values, "o-", color=color, label=metric.capitalize(),
                    markersize=4, linewidth=1.5)
        ax.set_title(SAMPLE_SHORT.get(sample_name, sample_name).replace("\n", " "),
                     fontsize=9, fontweight="bold")
        ax.set_ylim(0.90, 1.01)
        ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.2f"))
        if idx >= 5:
            ax.set_xlabel("TF-IDF Threshold")
        if idx % 5 == 0:
            ax.set_ylabel("Score")
        if idx == 0:
            ax.legend(loc="lower left", fontsize=7)

    fig.suptitle("Sensitivity Analysis: Precision / Recall / F1 vs TF-IDF Cosine Threshold",
                 fontsize=13, fontweight="bold")
    fig.tight_layout()
    fig.savefig(VIZ_DIR / "fig2_sensitivity_analysis.png", bbox_inches="tight")
    plt.close(fig)
    print("✓ fig2_sensitivity_analysis.png")


# ── 3. Aggregate P-R-F1 across thresholds ────────────────────────────────────
def plot_aggregate_sensitivity():
    fig, ax = plt.subplots(figsize=(8, 5))
    sens = iter2["sensitivity"]

    # Aggregate across all samples for each threshold
    thresholds = [r["threshold"] for r in list(sens.values())[0]]
    agg = {t: {"tp": 0, "fp": 0, "fn": 0} for t in thresholds}

    for records in sens.values():
        for r in records:
            agg[r["threshold"]]["tp"] += r["true_positive"]
            agg[r["threshold"]]["fp"] += r["false_positive"]
            agg[r["threshold"]]["fn"] += r["false_negative"]

    t_list, p_list, r_list, f1_list = [], [], [], []
    for t in thresholds:
        tp, fp, fn = agg[t]["tp"], agg[t]["fp"], agg[t]["fn"]
        p = tp / (tp + fp) if (tp + fp) else 1.0
        r = tp / (tp + fn) if (tp + fn) else 1.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        t_list.append(t)
        p_list.append(p)
        r_list.append(r)
        f1_list.append(f1)

    ax.plot(t_list, p_list, "s-", label="Precision", color="#4C72B0", markersize=6)
    ax.plot(t_list, r_list, "^-", label="Recall", color="#DD8452", markersize=6)
    ax.plot(t_list, f1_list, "D-", label="F1 Score", color="#55A868", markersize=6)
    ax.axvline(0.85, color="red", linestyle="--", linewidth=1, alpha=0.7, label="Selected threshold (0.85)")

    ax.set_xlabel("TF-IDF Cosine Similarity Threshold")
    ax.set_ylabel("Score")
    ax.set_title("Aggregate Precision / Recall / F1 vs TF-IDF Threshold (All Samples)",
                 fontweight="bold")
    ax.set_ylim(0.95, 1.005)
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.3f"))
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(VIZ_DIR / "fig3_aggregate_sensitivity.png", bbox_inches="tight")
    plt.close(fig)
    print("✓ fig3_aggregate_sensitivity.png")


# ── 4. Error breakdown (FP / FN per sample) ──────────────────────────────────
def plot_error_breakdown():
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    for ax, (tag, data, color, title) in zip(axes, [
        ("iter1", iter1, "#C44E52", "Iteration 1 (Baseline)"),
        ("iter2", iter2, "#55A868", "Iteration 2 (Refined)"),
    ]):
        errors = data["errors"]
        names = list(errors.keys())
        short = [SAMPLE_SHORT.get(n, n) for n in names]
        fp_counts = [errors[n]["fp_count"] for n in names]
        fn_counts = [errors[n]["fn_count"] for n in names]
        x = np.arange(len(names))
        w = 0.35

        ax.bar(x - w/2, fp_counts, w, label="False Positives", color="#C44E52", edgecolor="white")
        ax.bar(x + w/2, fn_counts, w, label="False Negatives", color="#4C72B0", edgecolor="white")
        ax.set_title(title, fontweight="bold")
        ax.set_xticks(x)
        ax.set_xticklabels(short, fontsize=7)
        ax.set_ylabel("Count")
        ax.legend()
        # Annotate total
        total_fp = sum(fp_counts)
        total_fn = sum(fn_counts)
        ax.text(0.98, 0.95, f"Total FP={total_fp}  FN={total_fn}",
                transform=ax.transAxes, ha="right", va="top",
                fontsize=9, bbox=dict(boxstyle="round,pad=0.3", fc="lightyellow", alpha=0.8))

    fig.suptitle("Error Breakdown: False Positives and False Negatives per Sample",
                 fontsize=13, fontweight="bold")
    fig.tight_layout()
    fig.savefig(VIZ_DIR / "fig4_error_breakdown.png", bbox_inches="tight")
    plt.close(fig)
    print("✓ fig4_error_breakdown.png")


# ── 5. Iteration progression summary ─────────────────────────────────────────
def plot_iteration_summary():
    fig, ax = plt.subplots(figsize=(8, 4))

    iterations = ["Iteration 1\n(Baseline)", "Iteration 2\n(Refined)"]
    p_vals = [iter1["aggregate"]["precision"], iter2["aggregate"]["precision"]]
    r_vals = [iter1["aggregate"]["recall"], iter2["aggregate"]["recall"]]
    f1_vals = [iter1["aggregate"]["f1"], iter2["aggregate"]["f1"]]

    x = np.arange(len(iterations))
    w = 0.25

    ax.bar(x - w, p_vals, w, label="Precision", color="#4C72B0", edgecolor="white")
    ax.bar(x, r_vals, w, label="Recall", color="#DD8452", edgecolor="white")
    ax.bar(x + w, f1_vals, w, label="F1 Score", color="#55A868", edgecolor="white")

    ax.set_ylim(0.995, 1.001)
    ax.set_xticks(x)
    ax.set_xticklabels(iterations)
    ax.set_ylabel("Score")
    ax.set_title("Aggregate Metrics: Iteration Progression", fontweight="bold")
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%.4f"))
    ax.legend(loc="lower right")
    ax.grid(True, alpha=0.3, axis="y")

    # Annotate exact values
    for xi, p, r, f1 in zip(x, p_vals, r_vals, f1_vals):
        ax.text(xi - w, p + 0.0002, f"{p:.4f}", ha="center", va="bottom", fontsize=8)
        ax.text(xi, r + 0.0002, f"{r:.4f}", ha="center", va="bottom", fontsize=8)
        ax.text(xi + w, f1 + 0.0002, f"{f1:.4f}", ha="center", va="bottom", fontsize=8)

    fig.tight_layout()
    fig.savefig(VIZ_DIR / "fig5_iteration_summary.png", bbox_inches="tight")
    plt.close(fig)
    print("✓ fig5_iteration_summary.png")


# ── 6. Heatmap: per-sample per-threshold F1 ──────────────────────────────────
def plot_threshold_heatmap():
    sens = iter2["sensitivity"]
    sample_names = list(sens.keys())
    short = [SAMPLE_SHORT.get(n, n).replace("\n", " ") for n in sample_names]
    thresholds = [r["threshold"] for r in list(sens.values())[0]]

    f1_matrix = []
    for name in sample_names:
        f1_matrix.append([r["f1"] for r in sens[name]])
    f1_matrix = np.array(f1_matrix)

    fig, ax = plt.subplots(figsize=(10, 6))
    im = ax.imshow(f1_matrix, cmap="RdYlGn", aspect="auto", vmin=0.95, vmax=1.0)

    ax.set_xticks(np.arange(len(thresholds)))
    ax.set_xticklabels([f"{t:.2f}" for t in thresholds])
    ax.set_yticks(np.arange(len(short)))
    ax.set_yticklabels(short, fontsize=8)
    ax.set_xlabel("TF-IDF Threshold")
    ax.set_title("F1 Score Heatmap: Sample × TF-IDF Threshold", fontweight="bold")

    # Annotate cells
    for i in range(len(sample_names)):
        for j in range(len(thresholds)):
            val = f1_matrix[i, j]
            color = "white" if val < 0.97 else "black"
            ax.text(j, i, f"{val:.3f}", ha="center", va="center", fontsize=7, color=color)

    fig.colorbar(im, ax=ax, label="F1 Score", shrink=0.8)
    fig.tight_layout()
    fig.savefig(VIZ_DIR / "fig6_threshold_heatmap.png", bbox_inches="tight")
    plt.close(fig)
    print("✓ fig6_threshold_heatmap.png")


# ── Run all ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    plot_per_sample_comparison()
    plot_sensitivity_curves()
    plot_aggregate_sensitivity()
    plot_error_breakdown()
    plot_iteration_summary()
    plot_threshold_heatmap()
    print(f"\nAll visualizations saved to {VIZ_DIR}")
