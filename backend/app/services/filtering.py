"""
app/services/filtering.py
─────────────────────────
Claude-based relevance filtering via the Anthropic API.
Production-grade: uses the API directly (no browser automation).
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
#  Prompt
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

_DECISION_RE = re.compile(r"^\s*(\d+)\s*:\s*(KEEP|REMOVE)", re.IGNORECASE | re.MULTILINE)


def _build_batch_prompt(papers: List[Tuple[int, str, int | None, str]]) -> str:
    lines = ["Papers:\n"]
    for idx, title, year, publisher in papers:
        lines.append(f"{idx}: {title}  ({year or '?'}, {publisher})")
    return "\n".join(lines)


def _parse_response(text: str) -> Dict[int, str]:
    decisions: Dict[int, str] = {}
    for m in _DECISION_RE.finditer(text):
        decisions[int(m.group(1))] = m.group(2).upper()
    return decisions


# ═══════════════════════════════════════════════════════════════════════════════
#  Service
# ═══════════════════════════════════════════════════════════════════════════════

def run_filtering_sync(
    db: Session,
    project_id: UUID,
    progress_cb: Optional[Callable[[float, str], None]] = None,
) -> dict:
    """
    Filter papers using Claude API.  Updates relevance field in-place.
    """
    import anthropic

    project = db.execute(
        select(Project).where(Project.id == project_id)
    ).scalar_one()

    # Only filter non-duplicate, pending papers
    papers = db.execute(
        select(Paper)
        .where(
            Paper.project_id == project_id,
            Paper.is_duplicate == False,  # noqa: E712
            Paper.relevance == RelevanceDecision.PENDING,
        )
        .order_by(Paper.id)
    ).scalars().all()

    if not papers:
        if progress_cb:
            progress_cb(100, "No papers to filter")
        return {"total": 0, "kept": 0, "removed": 0}

    total = len(papers)
    batch_size = settings.CLAUDE_BATCH_SIZE

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    kept = 0
    removed = 0
    batch_num = 0
    total_batches = (total + batch_size - 1) // batch_size

    for start in range(0, total, batch_size):
        batch_num += 1
        end = min(start + batch_size, total)
        batch_papers = papers[start:end]

        if progress_cb:
            pct = (batch_num / total_batches) * 90
            progress_cb(pct, f"Filtering batch {batch_num}/{total_batches}…")

        # Build prompt
        prompt_papers = []
        idx_map: Dict[int, Paper] = {}
        for i, paper in enumerate(batch_papers, 1):
            prompt_papers.append((i, paper.title, paper.year, paper.publisher))
            idx_map[i] = paper

        user_prompt = _build_batch_prompt(prompt_papers)

        # Call Claude
        try:
            response = client.messages.create(
                model=settings.CLAUDE_MODEL,
                max_tokens=4096,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            response_text = response.content[0].text
        except Exception as e:
            logger.error("Claude API error on batch %d: %s", batch_num, e)
            # Default to KEEP on API failure
            for paper in batch_papers:
                paper.relevance = RelevanceDecision.KEEP
                kept += 1
            db.flush()
            continue

        decisions = _parse_response(response_text)
        logger.info("Batch %d: parsed %d decisions", batch_num, len(decisions))

        for idx, paper in idx_map.items():
            decision = decisions.get(idx, "KEEP")
            if decision == "KEEP":
                paper.relevance = RelevanceDecision.KEEP
                kept += 1
            else:
                paper.relevance = RelevanceDecision.REMOVE
                removed += 1

        db.flush()

    # Update project stats
    project.total_after_filter = kept
    db.commit()

    if progress_cb:
        progress_cb(100, f"Filtering complete — {kept} kept, {removed} removed")

    return {"total": total, "kept": kept, "removed": removed}
