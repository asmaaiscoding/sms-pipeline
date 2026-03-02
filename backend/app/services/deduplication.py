"""
app/services/deduplication.py
─────────────────────────────
Deduplication service — runs inside Celery workers.
Two-pass: DOI exact match, then TF-IDF title similarity.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Callable, Optional
from uuid import UUID

import numpy as np
from sqlalchemy import select, update, func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.paper import Paper
from app.models.project import Project
from app.core.pipeline_logger import get_pipeline_logger

logger = logging.getLogger(__name__)
settings = get_settings()


def _norm_doi(doi: str) -> str:
    if not doi or not isinstance(doi, str):
        return ""
    doi = doi.strip().lower()
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)


def _norm_title(title: str) -> str:
    if not isinstance(title, str):
        return ""
    title = unicodedata.normalize("NFKD", title).lower().strip()
    title = re.sub(r"[^a-z0-9\s]", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def _completeness(paper: Paper) -> int:
    count = 0
    for attr in ("title", "authors", "year", "publisher", "doi", "link", "abstract"):
        val = getattr(paper, attr, None)
        if val and str(val).strip():
            count += 1
    return count


def run_deduplication_sync(
    db: Session,
    project_id: UUID,
    progress_cb: Optional[Callable[[float, str], None]] = None,
) -> dict:
    """
    Mark duplicate papers in-place (sets is_duplicate=True, duplicate_of_id).
    Returns stats dict.
    """
    project = db.execute(
        select(Project).where(Project.id == project_id)
    ).scalar_one()

    flog = get_pipeline_logger("deduplication", project.name)
    flog.info("=== Deduplication started for project '%s' ===", project.name)

    papers = db.execute(
        select(Paper)
        .where(Paper.project_id == project_id, Paper.is_duplicate == False)  # noqa: E712
        .order_by(Paper.created_at)
    ).scalars().all()

    if not papers:
        flog.info("No papers to deduplicate")
        return {"total": 0, "doi_removed": 0, "title_removed": 0}

    total = len(papers)
    if progress_cb:
        progress_cb(5, f"Deduplicating {total} papers — Pass 1: DOI matching…")

    # ── Pass 1: DOI-based ────────────────────────────────────────────────────
    doi_groups: dict[str, list[Paper]] = {}
    for p in papers:
        ndoi = _norm_doi(p.doi)
        if ndoi:
            doi_groups.setdefault(ndoi, []).append(p)

    doi_removed = 0
    for ndoi, group in doi_groups.items():
        if len(group) <= 1:
            continue
        best = max(group, key=_completeness)
        for p in group:
            if p.id != best.id:
                p.is_duplicate = True
                p.duplicate_of_id = best.id
                doi_removed += 1
                flog.info("  DOI dup: '%s' (doi=%s) — duplicate of '%s'", p.title, ndoi, best.title)

    db.flush()
    flog.info("--- DOI pass done: %d duplicates found ---", doi_removed)
    logger.info("DOI dedup removed %d", doi_removed)

    if progress_cb:
        remaining_count = total - doi_removed
        progress_cb(40, f"DOI pass: {doi_removed} duplicates found — {remaining_count} remaining. Pass 2: title similarity…")

    # ── Pass 2: Title similarity (TF-IDF cosine) ────────────────────────────
    remaining = [p for p in papers if not p.is_duplicate]
    titles = [_norm_title(p.title) for p in remaining]

    title_removed = 0
    if len(titles) >= 2:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity

        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 4))
        tfidf = vec.fit_transform(titles)
        sim = cosine_similarity(tfidf)
        np.fill_diagonal(sim, 0)

        marked: set = set()
        for i in range(len(remaining)):
            if remaining[i].id in marked:
                continue
            for j in range(i + 1, len(remaining)):
                if remaining[j].id in marked:
                    continue
                if sim[i, j] >= settings.TITLE_SIM_THRESHOLD:
                    if _completeness(remaining[i]) >= _completeness(remaining[j]):
                        loser = remaining[j]
                        winner = remaining[i]
                    else:
                        loser = remaining[i]
                        winner = remaining[j]
                    loser.is_duplicate = True
                    loser.duplicate_of_id = winner.id
                    marked.add(loser.id)
                    title_removed += 1
                    flog.info("  Title dup: '%s' ~ '%s' (sim=%.3f)", loser.title, winner.title, sim[i, j])
                    if loser.id == remaining[i].id:
                        break

    db.flush()
    flog.info("--- Title pass done: %d duplicates found ---", title_removed)
    logger.info("Title dedup removed %d", title_removed)

    # Update project stats
    non_dup_count = total - doi_removed - title_removed
    project.total_after_dedup = non_dup_count
    db.commit()

    flog.info("=== Dedup complete — %d unique (removed %d DOI + %d title) ===", non_dup_count, doi_removed, title_removed)

    if progress_cb:
        progress_cb(
            100,
            f"Dedup complete — {non_dup_count} unique papers "
            f"(removed {doi_removed} by DOI + {title_removed} by title similarity)"
        )

    return {
        "total": total,
        "doi_removed": doi_removed,
        "title_removed": title_removed,
        "remaining": non_dup_count,
    }
