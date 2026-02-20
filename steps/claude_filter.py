"""
steps/claude_filter.py
----------------------
Step 3 — Filter papers for relevance using Claude (browser automation).

Flow
----
1.  Open Chrome to claude.ai/new
2.  Wait for the user to log in (if not already)
3.  Send papers in batches with a filtering prompt
4.  Parse Claude's response to identify KEEP / REMOVE decisions
5.  Save the filtered xlsx

If full Selenium automation fails at any point the script falls back to a
*clipboard-assisted* workflow: it copies the prompt to the system clipboard,
asks the user to paste it into Claude manually, then reads the response back
from the clipboard.
"""

from __future__ import annotations

import os
import re
import sys
import time
from typing import Dict, List, Optional, Tuple

import pandas as pd

try:
    import pyperclip
    HAS_PYPERCLIP = True
except ImportError:
    HAS_PYPERCLIP = False

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from scrapers.base import setup_driver, pause_for_human
from config import CLAUDE_URL, CLAUDE_BATCH_SIZE, OUTPUT_DIR


# ═══════════════════════════════════════════════════════════════════════════════
#  Prompt builder
# ═══════════════════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """\
You are assisting with a **systematic mapping study** in the field of
**software security**.  Below is a numbered list of academic papers
(title, year, publisher).

**Your task**: decide which papers are **directly relevant** to software
security.

A paper is **RELEVANT** if it focuses on:
- Software vulnerability detection, analysis, testing, or remediation
- Security flaws in source code, applications, or software systems
- Tools, techniques, or methods for finding or fixing security issues in software
- Secure software development practices or security-aware refactoring
- Malware analysis of software artifacts

A paper is **NOT RELEVANT** if it:
- Focuses on network security, hardware security, IoT physical-layer security,
  or pure cryptography without a software angle
- Is about general software engineering without any security concern
- Covers non-software security topics (physical security, policy, governance)

**Output format** — for every paper respond with its number followed by
KEEP or REMOVE.  Example:

1: KEEP
2: REMOVE
3: KEEP

Do NOT add any other text — only the numbered KEEP/REMOVE list.
"""


def _build_batch_prompt(papers: List[Tuple[int, str, str, str]]) -> str:
    """Build the full prompt for one batch of papers.

    *papers* is a list of (global_index, title, year, publisher).
    """
    lines = [_SYSTEM_PROMPT, "\nPapers:\n"]
    for idx, title, year, publisher in papers:
        lines.append(f"{idx}: {title}  ({year}, {publisher})")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  Response parser
# ═══════════════════════════════════════════════════════════════════════════════

_DECISION_RE = re.compile(r"^\s*(\d+)\s*:\s*(KEEP|REMOVE)", re.IGNORECASE | re.MULTILINE)


def _parse_response(text: str) -> Dict[int, str]:
    """Return {paper_index: 'KEEP' | 'REMOVE'} parsed from Claude's reply."""
    decisions: Dict[int, str] = {}
    for m in _DECISION_RE.finditer(text):
        decisions[int(m.group(1))] = m.group(2).upper()
    return decisions


# ═══════════════════════════════════════════════════════════════════════════════
#  Selenium helpers for Claude's chat UI
# ═══════════════════════════════════════════════════════════════════════════════

# Selectors may need updating if Claude changes its frontend.
_INPUT_SELECTORS = [
    "div.ProseMirror[contenteditable='true']",
    "fieldset div[contenteditable='true']",
    "textarea",
    "div[contenteditable='true']",
]

_SEND_SELECTORS = [
    "button[aria-label='Send Message']",
    "button[aria-label='Send message']",
    "button[data-testid='send-button']",
    "button.send-button",
    "form button[type='submit']",
]


def _find_input(driver) -> Optional[object]:
    for sel in _INPUT_SELECTORS:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els:
                if el.is_displayed():
                    return el
        except Exception:
            continue
    return None


def _find_send_button(driver) -> Optional[object]:
    for sel in _SEND_SELECTORS:
        try:
            els = driver.find_elements(By.CSS_SELECTOR, sel)
            for el in els:
                if el.is_displayed():
                    return el
        except Exception:
            continue
    return None


def _wait_for_response(driver, timeout: int = 300) -> str:
    """Wait until Claude finishes generating, then return the last message text."""
    print("  Waiting for Claude to finish …")

    # Wait for the stop/generating indicator to appear then disappear
    time.sleep(5)  # give it a moment to start generating

    # Poll until generation stops
    deadline = time.time() + timeout
    while time.time() < deadline:
        # Check if still generating (stop button visible)
        generating = False
        try:
            stop_btns = driver.find_elements(
                By.CSS_SELECTOR,
                "button[aria-label='Stop Response'] , "
                "button[aria-label='Stop generating'] , "
                "button[aria-label='Stop']"
            )
            generating = any(b.is_displayed() for b in stop_btns)
        except Exception:
            pass

        if not generating:
            # Double-check after a short wait
            time.sleep(3)
            try:
                stop_btns = driver.find_elements(
                    By.CSS_SELECTOR,
                    "button[aria-label='Stop Response'] , "
                    "button[aria-label='Stop generating'] , "
                    "button[aria-label='Stop']"
                )
                if not any(b.is_displayed() for b in stop_btns):
                    break
            except Exception:
                break

        time.sleep(3)

    # Extract the last assistant message
    try:
        # Claude renders messages in divs; the last one should be the response
        msgs = driver.find_elements(
            By.CSS_SELECTOR,
            "[data-testid='chat-message-content'] , "
            "div.font-claude-message , "
            "div[class*='message'] div[class*='markdown'] , "
            "div.grid-cols-1 div.min-h-\\[20px\\]"
        )
        if msgs:
            return msgs[-1].text
    except Exception:
        pass

    # Fallback: grab all visible text from the chat
    try:
        chat = driver.find_element(
            By.CSS_SELECTOR,
            "main , div[class*='conversation'] , div[class*='chat']"
        )
        return chat.text
    except Exception:
        return ""


def _send_message_selenium(driver, text: str) -> bool:
    """Type *text* into Claude's input and click Send.  Return True on success."""
    inp = _find_input(driver)
    if inp is None:
        return False

    try:
        inp.click()
        time.sleep(0.5)

        # Use clipboard to paste (handles long text better than send_keys)
        if HAS_PYPERCLIP:
            pyperclip.copy(text)
            inp.send_keys(Keys.CONTROL, "v")
        else:
            # Fallback: inject via JS
            driver.execute_script(
                "arguments[0].innerText = arguments[1]; "
                "arguments[0].dispatchEvent(new Event('input', {bubbles: true}));",
                inp, text,
            )

        time.sleep(1)

        # Try clicking the send button
        btn = _find_send_button(driver)
        if btn:
            btn.click()
        else:
            inp.send_keys(Keys.RETURN)

        return True
    except Exception as exc:
        print(f"  [WARN] Selenium send failed: {exc}")
        return False


def _start_new_chat(driver) -> bool:
    """Navigate to a new Claude chat."""
    try:
        driver.get(CLAUDE_URL)
        time.sleep(5)
        return True
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
#  Clipboard-assisted fallback
# ═══════════════════════════════════════════════════════════════════════════════

def _clipboard_workflow(prompt: str, batch_num: int) -> str:
    """Copy prompt to clipboard, instruct user, read response from clipboard."""
    if HAS_PYPERCLIP:
        pyperclip.copy(prompt)
        print(f"\n  [Batch {batch_num}] Prompt copied to clipboard.")
    else:
        # write to file as last resort
        fname = f"_claude_prompt_batch_{batch_num}.txt"
        with open(fname, "w", encoding="utf-8") as f:
            f.write(prompt)
        print(f"\n  [Batch {batch_num}] Prompt saved to {fname}")
        print("  Open the file and copy its contents manually.")

    print("  1. Paste the prompt into Claude")
    print("  2. Wait for Claude to finish responding")
    print("  3. Select Claude's ENTIRE response and copy it (Ctrl+A → Ctrl+C)")
    input("  4. Press ENTER here once the response is in your clipboard …")

    if HAS_PYPERCLIP:
        return pyperclip.paste()

    print("  pyperclip not installed — please paste Claude's response below.")
    print("  Type END on a line by itself when done:")
    lines = []
    while True:
        line = input()
        if line.strip().upper() == "END":
            break
        lines.append(line)
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  Main entry point
# ═══════════════════════════════════════════════════════════════════════════════

def run_claude_filter(
    input_path: str,
    output_path: Optional[str] = None,
    batch_size: int = CLAUDE_BATCH_SIZE,
) -> str:
    """Filter papers via Claude.  Returns the output xlsx path."""
    if output_path is None:
        output_path = os.path.join(OUTPUT_DIR, "filtered.xlsx")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    df = pd.read_excel(input_path)
    total = len(df)
    print(f"\n[CLAUDE] {total} papers to filter")

    # ── open browser ──────────────────────────────────────────────────────────
    driver = setup_driver()
    selenium_ok = True

    try:
        driver.get(CLAUDE_URL)
        time.sleep(5)
        pause_for_human(
            "Please log in to Claude if needed.\n"
            "  Make sure a new chat is open and ready."
        )
    except Exception:
        selenium_ok = False
        print("[CLAUDE] Could not open Claude — falling back to clipboard mode.")

    # ── process in batches ────────────────────────────────────────────────────
    decisions: Dict[int, str] = {}
    batches = list(range(0, total, batch_size))

    for b_num, start in enumerate(batches, 1):
        end = min(start + batch_size, total)
        batch_papers = []
        for i in range(start, end):
            row = df.iloc[i]
            idx = i + 1  # 1-based for Claude
            title = str(row.get("Title", ""))
            year  = str(row.get("Year", ""))
            pub   = str(row.get("Publisher", ""))
            batch_papers.append((idx, title, year, pub))

        prompt = _build_batch_prompt(batch_papers)
        print(f"\n{'─' * 60}")
        print(f"  Batch {b_num}/{len(batches)}  (papers {start + 1}–{end})")
        print(f"{'─' * 60}")

        response_text = ""

        if selenium_ok:
            # try new chat for each batch (except the first)
            if b_num > 1:
                _start_new_chat(driver)
                time.sleep(3)

            sent = _send_message_selenium(driver, prompt)
            if sent:
                response_text = _wait_for_response(driver, timeout=300)

            if not response_text.strip():
                print("  [WARN] No response detected via Selenium — switching to clipboard.")
                selenium_ok = False

        if not selenium_ok or not response_text.strip():
            response_text = _clipboard_workflow(prompt, b_num)

        batch_decisions = _parse_response(response_text)
        print(f"  Parsed {len(batch_decisions)} decisions")
        decisions.update(batch_decisions)

        # Intermediate save
        _save_filtered(df, decisions, output_path)

    # ── final save ────────────────────────────────────────────────────────────
    try:
        driver.quit()
    except Exception:
        pass

    kept_path = _save_filtered(df, decisions, output_path)

    kept_count = sum(1 for d in decisions.values() if d == "KEEP")
    removed_count = sum(1 for d in decisions.values() if d == "REMOVE")
    unknown = total - len(decisions)

    print(f"\n[CLAUDE] Results:")
    print(f"  KEEP:    {kept_count}")
    print(f"  REMOVE:  {removed_count}")
    if unknown:
        print(f"  No decision (kept by default): {unknown}")
    print(f"  Output → {kept_path}")
    return kept_path


def _save_filtered(
    df: pd.DataFrame,
    decisions: Dict[int, str],
    output_path: str,
) -> str:
    """Keep only papers marked KEEP (or with no decision) and save."""
    mask = []
    for i in range(len(df)):
        dec = decisions.get(i + 1, "KEEP")  # default: keep if no decision
        mask.append(dec == "KEEP")

    df_filtered = df.loc[mask].reset_index(drop=True)
    df_filtered.to_excel(output_path, index=False, engine="openpyxl")
    return output_path


# ── stand-alone ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python -m steps.claude_filter input.xlsx [output.xlsx]")
    out = sys.argv[2] if len(sys.argv) >= 3 else None
    run_claude_filter(sys.argv[1], out)
