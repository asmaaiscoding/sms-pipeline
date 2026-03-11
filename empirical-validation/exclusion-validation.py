"""
empirical-validation/exclusion-validation.py
─────────────────────────────────────────────
V4. Automated Exclusion Accuracy Validation (Ollama — llama3.2:3b)

Procedure:
  1. Load pre-existing labeled samples from exclusion-samples/files/.
     Each sample has a Ground_Truth column.
  2. Call local Ollama (llama3.2:3b) with title (+abstract if available)
     to get a fresh INCLUDE/EXCLUDE decision for every paper.
  3. Save updated LLM_Decision column back into each xlsx.
  4. Compute: precision, recall, specificity, F1, Cohen's Kappa, accuracy.
  5. Analyze error types.  Generate plots + interpretation.

Outputs → empirical-validation/exclusion-samples/results/
"""

from __future__ import annotations

import os
import json
import time

import requests
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ── paths ────────────────────────────────────────────────────────────────────
BASE = os.path.dirname(os.path.abspath(__file__))
FILES_DIR = os.path.join(BASE, "exclusion-samples", "files")
RESULTS_DIR = os.path.join(BASE, "exclusion-samples", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

SAMPLE_SIZES = [50, 100, 150, 200, 250, 300]

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:3b"

EXCLUSION_PROMPT = """\
You are a strict academic paper screener for a Systematic Mapping Study focused \
exclusively on **SOFTWARE security** — meaning security concerns that arise \
within the SOFTWARE layer (source code, binaries, applications, libraries, \
frameworks, APIs, software development processes).

Task: Given the title and abstract of an academic paper, decide \
whether it should be INCLUDED or EXCLUDED.

── INCLUDE only if the paper's PRIMARY focus is one of these SOFTWARE-LEVEL topics:
• Vulnerability detection / analysis / classification in source code or binaries
• Software vulnerability remediation, patching, or repair
• Static analysis, dynamic analysis, symbolic execution, or taint analysis for security
• Fuzz testing / fuzzing of software components
• Secure coding practices, security-aware code review, or security refactoring
• Software penetration testing or security auditing of applications
• Malware analysis, reverse engineering of malicious software
• Security of web applications, mobile applications, desktop applications
• API security, authentication / authorization implementation flaws in software
• Software supply-chain security (dependency vulnerabilities, package managers)
• DevSecOps, CI/CD pipeline security, SAST/DAST/IAST tools
• Security-related software metrics, vulnerability prediction models in code
• Cryptographic API misuse or insecure cryptographic implementations in software

── EXCLUDE if the paper's PRIMARY focus is any of the following (even if it \
mentions "security"):
• Network security, firewall rules, intrusion detection/prevention systems (IDS/IPS)
• Hardware security, side-channel attacks on chips, TPM, TEE at hardware level
• IoT security focused on device/hardware/protocol layer, not software
• Cloud infrastructure security, VM isolation (unless it's about software config flaws)
• Cyber-physical systems security (SCADA, power grids) without a software code focus
• Wireless / radio / protocol-level security (Bluetooth, Zigbee, 5G)
• Pure cryptography theory, mathematical proofs, new cipher design
• Information security management, governance, risk frameworks, compliance, policy
• Privacy, GDPR, data protection law / regulations
• Social engineering, phishing awareness, human factors in security
• Digital forensics focused on disk/network/memory forensics (not software-level)
• General software engineering with no security dimension
• Biometrics, physical access control
• Any domain outside computer science (medicine, biology, finance, etc.)

── Decision rules:
1. If the title/abstract is ambiguous, lean toward EXCLUDE — we want high precision.
2. A paper that merely USES software but studies network/hardware/policy security \
is still EXCLUDED.
3. "Security testing" counts as INCLUDE only if it tests the software itself, not \
the network or infrastructure.

Respond with ONLY one word: INCLUDE or EXCLUDE

Title: "{title}"
Abstract: "{abstract}"
"""


def _call_ollama(title: str, abstract: str = "") -> str:
    """Call local Ollama and return 'KEEP' or 'REMOVE'."""
    prompt = EXCLUSION_PROMPT.format(
        title=title,
        abstract=abstract if abstract else "Not available",
    )
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 10},
    }

    for attempt in range(3):
        try:
            resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
            resp.raise_for_status()
            answer = resp.json().get("response", "").strip().upper()
            if "INCLUDE" in answer:
                return "KEEP"
            elif "EXCLUDE" in answer:
                return "REMOVE"
            else:
                return "KEEP"  # default to keep on ambiguous
        except requests.exceptions.RequestException:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            return "KEEP"
    return "KEEP"


# ── interpretation report ────────────────────────────────────────────────────

def write_interpretation(path: str, all_results: list, all_errors: dict):
    avg_p = np.mean([r["precision"] for r in all_results])
    avg_r = np.mean([r["recall"] for r in all_results])
    avg_f1 = np.mean([r["f1"] for r in all_results])
    avg_k = np.mean([r["cohens_kappa"] for r in all_results])
    avg_acc = np.mean([r["accuracy"] for r in all_results])
    avg_spec = np.mean([r["specificity"] for r in all_results])

    lines = []
    lines.append("=" * 70)
    lines.append("AUTOMATED EXCLUSION VALIDATION -- INTERPRETATION OF RESULTS")
    lines.append("=" * 70)

    lines.append("")
    lines.append("1. OVERVIEW")
    lines.append("-" * 40)
    lines.append(f"Number of samples evaluated: {len(all_results)}")
    lines.append(f"Sample sizes: {', '.join(str(r['sample_size']) for r in all_results)}")
    lines.append(f"Exclusion method: LLM-based (local Ollama, {OLLAMA_MODEL})")
    lines.append(f"Input: title + abstract (abstract='Not available' when missing)")
    lines.append(f"Ground truth: keyword-based relevance labeling (KEEP / REMOVE)")
    lines.append("")
    for r in all_results:
        lines.append(f"  Size {r['sample_size']:>3d}: "
                     f"P={r['precision']:.3f}  R={r['recall']:.3f}  F1={r['f1']:.3f}  "
                     f"Kappa={r['cohens_kappa']:.3f}  Acc={r['accuracy']:.3f}")

    lines.append("")
    lines.append("2. AGGREGATE PERFORMANCE")
    lines.append("-" * 40)
    lines.append(f"  Average Precision   : {avg_p:.3f}")
    lines.append(f"  Average Recall      : {avg_r:.3f}")
    lines.append(f"  Average F1          : {avg_f1:.3f}")
    lines.append(f"  Average Specificity : {avg_spec:.3f}")
    lines.append(f"  Average Cohen's Kappa: {avg_k:.3f}")
    lines.append(f"  Average Accuracy    : {avg_acc:.3f}")
    lines.append("")

    if avg_p > 0.80:
        lines.append("  Precision is HIGH -- when the LLM decides to KEEP a paper, it is almost always correct.")
    elif avg_p > 0.50:
        lines.append("  Precision is MODERATE -- a substantial fraction of papers the LLM marks KEEP")
        lines.append("  are actually irrelevant (false inclusions).")
    else:
        lines.append("  Precision is LOW -- most papers the LLM marks as KEEP are actually irrelevant.")
        lines.append("  This means the LLM is too conservative in excluding papers.")

    if avg_r > 0.80:
        lines.append("  Recall is HIGH -- the LLM correctly identifies most relevant papers.")
    elif avg_r > 0.50:
        lines.append("  Recall is MODERATE -- a notable fraction of relevant papers are being excluded.")
    else:
        lines.append("  Recall is LOW -- many relevant papers are being incorrectly excluded.")
        lines.append("  This is a significant concern for SMS completeness.")

    lines.append("")
    if avg_k > 0.80:
        lines.append(f"  Cohen's Kappa ({avg_k:.3f}) indicates ALMOST PERFECT agreement with ground truth.")
    elif avg_k > 0.60:
        lines.append(f"  Cohen's Kappa ({avg_k:.3f}) indicates SUBSTANTIAL agreement with ground truth.")
    elif avg_k > 0.40:
        lines.append(f"  Cohen's Kappa ({avg_k:.3f}) indicates MODERATE agreement with ground truth.")
    elif avg_k > 0.20:
        lines.append(f"  Cohen's Kappa ({avg_k:.3f}) indicates FAIR agreement with ground truth.")
    else:
        lines.append(f"  Cohen's Kappa ({avg_k:.3f}) indicates SLIGHT or POOR agreement with ground truth.")

    lines.append("")
    total_keep = sum(r["tp"] + r["fn"] for r in all_results)
    total_remove = sum(r["fp"] + r["tn"] for r in all_results)
    total_all = total_keep + total_remove
    keep_pct = total_keep / total_all * 100 if total_all > 0 else 0
    lines.append(f"  Class distribution: {keep_pct:.1f}% KEEP / {100 - keep_pct:.1f}% REMOVE")
    if keep_pct < 20 or keep_pct > 80:
        lines.append(f"  WARNING: Class imbalance is significant ({keep_pct:.0f}% vs {100-keep_pct:.0f}%).")
        lines.append(f"  High accuracy ({avg_acc:.3f}) is partly driven by the majority class (REMOVE).")
        lines.append(f"  F1 and Cohen's Kappa are more reliable measures in this scenario.")

    lines.append("")
    lines.append("3. ERROR ANALYSIS")
    lines.append("-" * 40)
    total_fn = sum(r["fn"] for r in all_results)
    total_fp = sum(r["fp"] for r in all_results)
    lines.append(f"  Total false exclusions (FN -- relevant papers wrongly removed): {total_fn}")
    lines.append(f"  Total false inclusions (FP -- irrelevant papers wrongly kept) : {total_fp}")
    lines.append("")
    if total_fn > total_fp:
        lines.append("  The LLM is more prone to FALSE EXCLUSIONS (removing relevant papers).")
        lines.append("  This is the more costly error type in an SMS, as it reduces recall/completeness.")
    elif total_fp > total_fn:
        lines.append("  The LLM is more prone to FALSE INCLUSIONS (keeping irrelevant papers).")
        lines.append("  This adds noise but does not threaten completeness of the review.")
    else:
        lines.append("  False exclusions and false inclusions are balanced.")

    for size in sorted(all_errors.keys()):
        errs = all_errors[size]
        if errs["false_exclusions"] or errs["false_inclusions"]:
            lines.append(f"\n  Sample {size}:")
            if errs["false_exclusions"]:
                lines.append(f"    False exclusions ({len(errs['false_exclusions'])}):")
                for t in errs["false_exclusions"][:3]:
                    lines.append(f"      - {t}")
            if errs["false_inclusions"]:
                lines.append(f"    False inclusions ({len(errs['false_inclusions'])}):")
                for t in errs["false_inclusions"][:3]:
                    lines.append(f"      - {t}")

    lines.append("")
    lines.append("4. CONCLUSION")
    lines.append("-" * 40)
    if avg_f1 > 0.70:
        lines.append("  The LLM-based exclusion pipeline performs WELL overall.")
    elif avg_f1 > 0.45:
        lines.append("  The LLM-based exclusion pipeline has MODERATE performance.")
        lines.append("  Manual review of excluded papers is recommended to ensure no")
        lines.append("  relevant studies are lost.")
    else:
        lines.append("  The LLM-based exclusion pipeline has LIMITED effectiveness.")
        lines.append("  The low F1 score suggests the LLM struggles to distinguish relevant")
        lines.append("  from irrelevant papers. This may be due to:")
        lines.append("    - Class imbalance (few relevant papers in large exclusion pools)")
        lines.append("    - Ambiguous paper titles/abstracts that challenge keyword-based ground truth")
        lines.append("    - Prompt design not being specific enough for the SMS criteria")
        lines.append("  Manual verification of all excluded papers is strongly recommended.")

    lines.append("")
    lines.append("=" * 70)

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ── metrics ──────────────────────────────────────────────────────────────────

def compute_metrics(gt: list[str], pred: list[str]) -> dict:
    """Compute precision, recall, specificity, F1, Cohen's Kappa, accuracy."""
    assert len(gt) == len(pred)
    n = len(gt)

    tp = sum(1 for g, p in zip(gt, pred) if g == "KEEP" and p == "KEEP")
    fp = sum(1 for g, p in zip(gt, pred) if g == "REMOVE" and p == "KEEP")
    fn = sum(1 for g, p in zip(gt, pred) if g == "KEEP" and p == "REMOVE")
    tn = sum(1 for g, p in zip(gt, pred) if g == "REMOVE" and p == "REMOVE")

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    specificity = tn / (tn + fp) if (tn + fp) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    po = (tp + tn) / n if n > 0 else 0
    pe_keep = ((tp + fp) / n) * ((tp + fn) / n) if n > 0 else 0
    pe_remove = ((fn + tn) / n) * ((fp + tn) / n) if n > 0 else 0
    pe = pe_keep + pe_remove
    kappa = (po - pe) / (1 - pe) if (1 - pe) > 0 else 0.0

    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "specificity": round(specificity, 4),
        "f1": round(f1, 4),
        "cohens_kappa": round(kappa, 4),
        "accuracy": round((tp + tn) / n, 4) if n > 0 else 0.0,
    }


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("V4. AUTOMATED EXCLUSION ACCURACY VALIDATION (Ollama)")
    print("=" * 70)

    # ── Load samples & run Ollama ────────────────────────────────────────────
    samples: list[tuple[int, pd.DataFrame]] = []
    for size in SAMPLE_SIZES:
        path = os.path.join(FILES_DIR, f"sample_{size}.xlsx")
        if not os.path.exists(path):
            print(f"  WARNING: {path} not found, skipping.")
            continue
        df = pd.read_excel(path, engine="openpyxl")
        if "Ground_Truth" not in df.columns:
            print(f"  WARNING: sample_{size}.xlsx has no Ground_Truth column, skipping.")
            continue

        n_keep = (df["Ground_Truth"] == "KEEP").sum()
        n_remove = (df["Ground_Truth"] == "REMOVE").sum()
        print(f"\n  Sample {size:>3d}: {len(df)} papers  ({n_keep} KEEP / {n_remove} REMOVE)")
        print(f"    Calling Ollama ({OLLAMA_MODEL}) for {len(df)} papers...")

        decisions = []
        for idx, row in df.iterrows():
            title = str(row.get("Title", ""))
            abstract = str(row.get("Abstract", "")) if "Abstract" in df.columns else ""
            decision = _call_ollama(title, abstract)
            decisions.append(decision)
            if (idx + 1) % 25 == 0:
                print(f"      {idx + 1}/{len(df)} done")

        df["LLM_Decision"] = decisions
        df.to_excel(path, index=False, engine="openpyxl")
        print(f"    Done — saved LLM_Decision to {os.path.basename(path)}")

        samples.append((size, df))

    if not samples:
        print("ERROR: No sample files found.")
        return

    # ── Compute metrics ──────────────────────────────────────────────────────
    print(f"\n{'─' * 70}")
    print("EXCLUSION RESULTS")
    print(f"{'─' * 70}")

    all_results = []
    all_errors: dict[int, dict] = {}

    for size, df in samples:
        gt = df["Ground_Truth"].tolist()
        pred = df["LLM_Decision"].tolist()
        metrics = compute_metrics(gt, pred)
        metrics["sample_size"] = size
        all_results.append(metrics)

        print(f"  Size {size:>3d}: P={metrics['precision']:.3f}  R={metrics['recall']:.3f}"
              f"  Spec={metrics['specificity']:.3f}  F1={metrics['f1']:.3f}"
              f"  Kappa={metrics['cohens_kappa']:.3f}  Acc={metrics['accuracy']:.3f}")
        print(f"           TP={metrics['tp']}  FP={metrics['fp']}  FN={metrics['fn']}  TN={metrics['tn']}")

        false_exclusions = []
        false_inclusions = []
        for j, row in df.iterrows():
            if row["Ground_Truth"] == "KEEP" and row["LLM_Decision"] == "REMOVE":
                false_exclusions.append(str(row["Title"])[:120])
            elif row["Ground_Truth"] == "REMOVE" and row["LLM_Decision"] == "KEEP":
                false_inclusions.append(str(row["Title"])[:120])

        all_errors[size] = {
            "false_exclusions": false_exclusions,
            "false_inclusions": false_inclusions,
        }

        if false_exclusions:
            print(f"           False exclusions ({len(false_exclusions)}):")
            for t in false_exclusions[:3]:
                print(f"             - {t}")
        if false_inclusions:
            print(f"           False inclusions ({len(false_inclusions)}):")
            for t in false_inclusions[:3]:
                print(f"             - {t}")

    avg_p = np.mean([r["precision"] for r in all_results])
    avg_r = np.mean([r["recall"] for r in all_results])
    avg_f1 = np.mean([r["f1"] for r in all_results])
    avg_k = np.mean([r["cohens_kappa"] for r in all_results])
    avg_acc = np.mean([r["accuracy"] for r in all_results])
    print(f"\n  AVERAGE:  P={avg_p:.3f}  R={avg_r:.3f}  F1={avg_f1:.3f}"
          f"  Kappa={avg_k:.3f}  Acc={avg_acc:.3f}")

    # ── Plots ────────────────────────────────────────────────────────────────
    plt.rcParams.update({
        "font.size": 11,
        "axes.titlesize": 13,
        "axes.labelsize": 12,
        "xtick.labelsize": 10,
        "ytick.labelsize": 10,
        "legend.fontsize": 10,
        "figure.facecolor": "white",
    })

    sizes = [r["sample_size"] for r in all_results]

    # ── Figure 1: Metrics overview ───────────────────────────────────────────
    fig1, axes1 = plt.subplots(1, 2, figsize=(14, 5))

    # Grouped bar: P / R / F1 per sample size
    x = np.arange(len(sizes))
    w = 0.22
    p_vals = [r["precision"] for r in all_results]
    r_vals = [r["recall"] for r in all_results]
    f1_vals = [r["f1"] for r in all_results]

    axes1[0].bar(x - w, p_vals, w, label="Precision", color="#4C72B0", edgecolor="white", linewidth=0.5)
    axes1[0].bar(x, r_vals, w, label="Recall", color="#55A868", edgecolor="white", linewidth=0.5)
    axes1[0].bar(x + w, f1_vals, w, label="F1", color="#C44E52", edgecolor="white", linewidth=0.5)
    axes1[0].set_xlabel("Sample Size")
    axes1[0].set_ylabel("Score")
    axes1[0].set_title("Precision / Recall / F1 by Sample Size")
    axes1[0].set_xticks(x)
    axes1[0].set_xticklabels(sizes)
    axes1[0].set_ylim(0, 1.15)
    axes1[0].legend(loc="upper right", framealpha=0.9)
    axes1[0].grid(True, alpha=0.2, axis="y")
    # Value labels
    for bars in [axes1[0].containers[i] for i in range(3)]:
        for bar in bars:
            h = bar.get_height()
            if h > 0:
                axes1[0].text(bar.get_x() + bar.get_width() / 2, h + 0.01,
                              f"{h:.2f}", ha="center", va="bottom", fontsize=8)

    # Specificity + Kappa + Accuracy
    axes1[1].plot(sizes, [r["specificity"] for r in all_results], "D-",
                  label="Specificity", color="#8172B3", markersize=7, linewidth=2)
    axes1[1].plot(sizes, [r["cohens_kappa"] for r in all_results], "v-",
                  label="Cohen's κ", color="#DD8452", markersize=7, linewidth=2)
    axes1[1].plot(sizes, [r["accuracy"] for r in all_results], "s-",
                  label="Accuracy", color="#4C72B0", markersize=7, linewidth=2)
    axes1[1].set_xlabel("Sample Size")
    axes1[1].set_ylabel("Score")
    axes1[1].set_title("Specificity / Cohen's κ / Accuracy by Sample Size")
    axes1[1].legend(framealpha=0.9)
    axes1[1].set_ylim(0, 1.05)
    axes1[1].grid(True, alpha=0.2)

    fig1.tight_layout(pad=2.0)
    fig1.savefig(os.path.join(RESULTS_DIR, "metrics_overview.png"), dpi=150, bbox_inches="tight")
    plt.close(fig1)
    print(f"\n  Plot saved -> exclusion-samples/results/metrics_overview.png")

    # ── Figure 2: Error analysis ─────────────────────────────────────────────
    fig2, axes2 = plt.subplots(1, 2, figsize=(14, 5))

    fn_vals = [r["fn"] for r in all_results]
    fp_vals = [r["fp"] for r in all_results]

    # Absolute counts
    w2 = 0.35
    bars_fn = axes2[0].bar(x - w2 / 2, fn_vals, w2, label="False Exclusions (FN)",
                           color="#C44E52", edgecolor="white", linewidth=0.5)
    bars_fp = axes2[0].bar(x + w2 / 2, fp_vals, w2, label="False Inclusions (FP)",
                           color="#DD8452", edgecolor="white", linewidth=0.5)
    for bar in list(bars_fn) + list(bars_fp):
        h = bar.get_height()
        if h > 0:
            axes2[0].text(bar.get_x() + bar.get_width() / 2, h + 0.3,
                          str(int(h)), ha="center", va="bottom", fontsize=9, fontweight="bold")
    axes2[0].set_xlabel("Sample Size")
    axes2[0].set_ylabel("Count")
    axes2[0].set_title("Error Counts by Sample Size")
    axes2[0].set_xticks(x)
    axes2[0].set_xticklabels(sizes)
    axes2[0].legend(framealpha=0.9)
    axes2[0].grid(True, alpha=0.2, axis="y")

    # Error rates
    fe_rates = [r["fn"] / (r["fn"] + r["tp"]) if (r["fn"] + r["tp"]) > 0 else 0 for r in all_results]
    fi_rates = [r["fp"] / (r["fp"] + r["tn"]) if (r["fp"] + r["tn"]) > 0 else 0 for r in all_results]
    axes2[1].plot(sizes, fe_rates, "o-", label="False Exclusion Rate (FN / total relevant)",
                  color="#C44E52", markersize=7, linewidth=2)
    axes2[1].plot(sizes, fi_rates, "s-", label="False Inclusion Rate (FP / total irrelevant)",
                  color="#DD8452", markersize=7, linewidth=2)
    axes2[1].set_xlabel("Sample Size")
    axes2[1].set_ylabel("Rate")
    axes2[1].set_title("Error Rates (Proportional)")
    axes2[1].legend(framealpha=0.9)
    axes2[1].set_ylim(-0.05, 1.05)
    axes2[1].grid(True, alpha=0.2)

    fig2.tight_layout(pad=2.0)
    fig2.savefig(os.path.join(RESULTS_DIR, "error_analysis.png"), dpi=150, bbox_inches="tight")
    plt.close(fig2)
    print(f"  Plot saved -> exclusion-samples/results/error_analysis.png")

    # ── Figure 3: Confusion matrix heatmaps ──────────────────────────────────
    n_samples = len(all_results)
    fig3, axes3 = plt.subplots(1, n_samples, figsize=(4 * n_samples, 3.5))
    if n_samples == 1:
        axes3 = [axes3]

    for ax, r in zip(axes3, all_results):
        cm = np.array([[r["tp"], r["fn"]], [r["fp"], r["tn"]]])
        im = ax.imshow(cm, cmap="Blues", vmin=0)
        ax.set_xticks([0, 1])
        ax.set_yticks([0, 1])
        ax.set_xticklabels(["KEEP", "REMOVE"])
        ax.set_yticklabels(["KEEP", "REMOVE"])
        ax.set_xlabel("Predicted")
        ax.set_ylabel("Ground Truth")
        ax.set_title(f"n = {r['sample_size']}", fontsize=11)
        for i in range(2):
            for j in range(2):
                val = cm[i, j]
                color = "white" if val > cm.max() * 0.6 else "black"
                ax.text(j, i, str(val), ha="center", va="center",
                        fontsize=13, fontweight="bold", color=color)

    fig3.suptitle("Confusion Matrices by Sample Size", fontsize=14, y=1.02)
    fig3.tight_layout(pad=1.5)
    fig3.savefig(os.path.join(RESULTS_DIR, "confusion_matrices.png"), dpi=150, bbox_inches="tight")
    plt.close(fig3)
    print(f"  Plot saved -> exclusion-samples/results/confusion_matrices.png")

    # ── Save results ─────────────────────────────────────────────────────────
    results_summary = {
        "sample_sizes": [r["sample_size"] for r in all_results],
        "results": all_results,
        "averages": {
            "precision": round(avg_p, 4),
            "recall": round(avg_r, 4),
            "f1": round(avg_f1, 4),
            "cohens_kappa": round(avg_k, 4),
            "accuracy": round(avg_acc, 4),
        },
        "error_examples": {str(k): v for k, v in all_errors.items()},
    }
    with open(os.path.join(RESULTS_DIR, "validation_results.json"), "w") as f:
        json.dump(results_summary, f, indent=2)
    print(f"  Results saved -> exclusion-samples/results/validation_results.json")

    results_df = pd.DataFrame(all_results)
    results_df.to_excel(os.path.join(RESULTS_DIR, "validation_results.xlsx"), index=False, engine="openpyxl")
    print(f"  Results table saved -> exclusion-samples/results/validation_results.xlsx")

    # ── Write interpretation ─────────────────────────────────────────────────
    interp_path = os.path.join(RESULTS_DIR, "interpretation.txt")
    write_interpretation(interp_path, all_results, all_errors)
    print(f"  Interpretation saved -> exclusion-samples/results/interpretation.txt")

    print(f"\n{'=' * 70}")
    print("V4 VALIDATION COMPLETE")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
