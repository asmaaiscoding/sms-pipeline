"""
app/services/filtering.py
─────────────────────────
Manual-filtering workflow.

Instead of calling the Claude API (which requires a paid key), this step
simply marks all non-duplicate papers as PENDING and tells the user to:

1. Download the XLSX from the Export button.
2. Paste the papers into Claude / ChatGPT with the provided prompt.
3. Re-import the KEEP/REMOVE decisions (or manually toggle them in the UI).
"""

from __future__ import annotations

import logging
import re
from typing import Callable, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.paper import Paper, RelevanceDecision
from app.models.project import Project

logger = logging.getLogger(__name__)
settings = get_settings()

# ═══════════════════════════════════════════════════════════════════════════════
#  Prompt  (for the user to copy-paste into Claude / ChatGPT)
# ═══════════════════════════════════════════════════════════════════════════════

FILTERING_PROMPT = """\
You are assisting with a **systematic mapping study** in the field of
**software security**.  I will give you an Excel spreadsheet (or a list)
of academic papers (title, year, publisher).

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

For every paper, add a column called "Relevance" and write KEEP or REMOVE.
Return the full updated spreadsheet / list.
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  Service
# ═══════════════════════════════════════════════════════════════════════════════

def run_filtering_sync(
    db: Session,
    project_id: UUID,
    progress_cb: Optional[Callable[[float, str], None]] = None,
) -> dict:
    """
    "Filter" step — NO API key needed.

    Counts non-duplicate papers, keeps them all as PENDING, and returns a
    message instructing the user to download the XLSX and use Claude / ChatGPT
    manually with FILTERING_PROMPT.
    """
    project = db.execute(
        select(Project).where(Project.id == project_id)
    ).scalar_one()

    papers = db.execute(
        select(Paper)
        .where(
            Paper.project_id == project_id,
            Paper.is_duplicate == False,  # noqa: E712
        )
        .order_by(Paper.id)
    ).scalars().all()

    total = len(papers)

    if not papers:
        if progress_cb:
            progress_cb(100, "No papers to filter")
        return {"total": 0, "pending": 0, "message": "No papers found."}

    if progress_cb:
        progress_cb(50, "Preparing papers for manual review…")

    # Ensure every non-duplicate paper is marked PENDING so the user can
    # set KEEP / REMOVE after the manual Claude step.
    pending_count = 0
    for paper in papers:
        if paper.relevance == RelevanceDecision.PENDING:
            pending_count += 1

    # Update project stats (before manual filtering, count = total unique)
    project.total_after_filter = total
    db.commit()

    if progress_cb:
        progress_cb(
            100,
            f"Done — {total} papers ready. Download the XLSX and use Claude/ChatGPT to filter them.",
        )

    return {
        "total": total,
        "pending": pending_count,
        "message": (
            f"{total} unique papers are ready for manual relevance filtering. "
            "Download the XLSX file, upload it to Claude or ChatGPT with the "
            "provided prompt, then update the Relevance column in the app."
        ),
        "prompt": FILTERING_PROMPT,
    }
