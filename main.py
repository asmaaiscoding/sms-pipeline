#!/usr/bin/env python3
"""
main.py — SMS Pipeline
=======================
Interactive CLI that orchestrates the four steps of a Systematic Mapping Study:

  1. Collect papers       (ACM · IEEE · Springer)
  2. Deduplicate
  3. Automated Exclusion  (Groq API)
  4. Find PDF links       (OpenAlex · arXiv · Semantic Scholar · BASE)

Run:
    python main.py
"""

import os
import sys
from typing import Dict, List, Optional

from config import (
    DETECTION_STRINGS,
    REMEDIATION_STRINGS,
    DEFAULT_YEAR_START,
    DEFAULT_YEAR_END,
    OUTPUT_DIR,
)
from steps.collect import run_collection
from steps.deduplicate import run_deduplication
from steps.find_links import run_link_finder


# ═══════════════════════════════════════════════════════════════════════════════
#  Input helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _hr():
    print("─" * 60)


def _banner():
    print()
    print("╔══════════════════════════════════════════════════════════╗")
    print("║         SMS Pipeline – Systematic Mapping Study         ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()


def _ask_int(prompt: str, default: int) -> int:
    raw = input(f"  {prompt} [{default}]: ").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print(f"  Invalid — using {default}")
        return default


def _ask_choice(prompt: str, options: List[str]) -> int:
    """Display *options* and return the 1-based index chosen."""
    for i, opt in enumerate(options, 1):
        print(f"    {i}. {opt}")
    while True:
        raw = input(f"  {prompt}: ").strip()
        try:
            idx = int(raw)
            if 1 <= idx <= len(options):
                return idx
        except ValueError:
            pass
        print(f"  Please enter a number between 1 and {len(options)}")


def _ask_yes_no(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    raw = input(f"  {prompt} [{hint}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes", "o", "oui")


# ═══════════════════════════════════════════════════════════════════════════════
#  Topic / search-string selection
# ═══════════════════════════════════════════════════════════════════════════════

def _choose_search_strings() -> Dict[str, str]:
    print("\n  Select topic:")
    choice = _ask_choice(
        "Choice",
        [
            "Vulnerability Detection",
            "Vulnerability Remediation",
            "Custom (enter your own search strings)",
        ],
    )

    if choice == 1:
        strings = DETECTION_STRINGS
        print(f"\n  Using {len(strings)} detection search strings.")
    elif choice == 2:
        strings = REMEDIATION_STRINGS
        print(f"\n  Using {len(strings)} remediation search strings.")
    else:
        strings = _enter_custom_strings()

    # Show strings
    for label, q in strings.items():
        print(f"    {label}: {q[:80]}{'…' if len(q) > 80 else ''}")
    return strings


def _enter_custom_strings() -> Dict[str, str]:
    print("\n  Enter your search strings one per line.")
    print("  Use boolean operators: AND, OR, and quoted phrases.")
    print("  Type DONE on a separate line when finished.\n")

    strings: Dict[str, str] = {}
    idx = 1
    while True:
        raw = input(f"  String {idx}: ").strip()
        if raw.upper() == "DONE":
            break
        if raw:
            strings[f"CUSTOM-{idx}"] = raw
            idx += 1

    if not strings:
        print("  No strings entered — falling back to Detection defaults.")
        return DETECTION_STRINGS

    return strings


# ═══════════════════════════════════════════════════════════════════════════════
#  Source selection
# ═══════════════════════════════════════════════════════════════════════════════

def _choose_sources() -> List[str]:
    print("\n  Select data sources:")
    choice = _ask_choice(
        "Choice",
        [
            "All (ACM + IEEE + Springer)",
            "ACM Digital Library only",
            "IEEE Xplore only",
            "Springer Link only",
            "Pick multiple …",
        ],
    )
    if choice == 1:
        return ["acm", "ieee", "springer"]
    if choice == 2:
        return ["acm"]
    if choice == 3:
        return ["ieee"]
    if choice == 4:
        return ["springer"]

    # pick multiple
    srcs: List[str] = []
    for label, key in [("ACM", "acm"), ("IEEE", "ieee"), ("Springer", "springer")]:
        if _ask_yes_no(f"Include {label}?", True):
            srcs.append(key)
    return srcs or ["acm", "ieee", "springer"]


# ═══════════════════════════════════════════════════════════════════════════════
#  Year range
# ═══════════════════════════════════════════════════════════════════════════════

def _choose_years():
    print(f"\n  Year range (default {DEFAULT_YEAR_START}–{DEFAULT_YEAR_END}):")
    start = _ask_int("Start year", DEFAULT_YEAR_START)
    end   = _ask_int("End year",   DEFAULT_YEAR_END)
    if start > end:
        start, end = end, start
    return start, end


# ═══════════════════════════════════════════════════════════════════════════════
#  Step selection
# ═══════════════════════════════════════════════════════════════════════════════

def _choose_step() -> int:
    print("  What would you like to do?\n")
    return _ask_choice(
        "Choice",
        [
            "Run full pipeline  (collect → deduplicate → filter → find links)",
            "Step 1 only: Collect papers",
            "Step 2 only: Deduplicate",
            "Step 3 only: Automated Exclusion",
            "Step 4 only: Find PDF links",
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Pipeline runner
# ═══════════════════════════════════════════════════════════════════════════════

def _ask_input_file(step_name: str, default: str) -> str:
    path = input(f"  Input xlsx for {step_name} [{default}]: ").strip()
    if not path:
        path = default
    if not os.path.isfile(path):
        print(f"  WARNING: {path} does not exist. Proceeding anyway.")
    return path


def main():
    _banner()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    step = _choose_step()
    # step: 1 = full pipeline, 2 = collect, 3 = dedup, 4 = exclusion, 5 = links
    run_all = (step == 1)

    collected_path = os.path.join(OUTPUT_DIR, "all_collected.xlsx")
    dedup_path     = os.path.join(OUTPUT_DIR, "deduplicated.xlsx")
    filtered_path  = os.path.join(OUTPUT_DIR, "filtered.xlsx")
    final_path     = os.path.join(OUTPUT_DIR, "final_with_links.xlsx")

    # ── Step 1: Collect ───────────────────────────────────────────────────────
    if run_all or step == 2:
        _hr()
        strings = _choose_search_strings()
        sources = _choose_sources()
        year_start, year_end = _choose_years()

        _hr()
        print("\n  Ready to collect papers:")
        print(f"    Sources:      {', '.join(sources)}")
        print(f"    Years:        {year_start}–{year_end}")
        print(f"    Strings:      {len(strings)}")
        print(f"    Output dir:   {OUTPUT_DIR}/")
        if not _ask_yes_no("Proceed?", True):
            print("  Aborted.")
            return
        collected_path = run_collection(strings, year_start, year_end, sources)

    # ── Step 2: Deduplicate ───────────────────────────────────────────────────
    if run_all or step == 3:
        _hr()
        if step == 3:
            collected_path = _ask_input_file("deduplication", collected_path)
        dedup_path = run_deduplication(collected_path, dedup_path)

    # ── Step 3: Automated Exclusion ─────────────────────────────────────────────
    if run_all or step == 4:
        _hr()
        if step == 4:
            dedup_path = _ask_input_file("automated exclusion", dedup_path)
        # Automated exclusion is handled by the web pipeline (Groq API).
        # In CLI mode, this step is a no-op — use the web UI instead.
        print("  Automated exclusion is only available via the web UI (Groq API).")
        filtered_path = dedup_path

    # ── Step 4: PDF links ─────────────────────────────────────────────────────
    if run_all or step == 5:
        _hr()
        if step == 5:
            filtered_path = _ask_input_file("link finder", filtered_path)
        run_link_finder(filtered_path, final_path)

    _hr()
    print("\n  Pipeline complete.  All output files are in the output/ folder.\n")


if __name__ == "__main__":
    main()