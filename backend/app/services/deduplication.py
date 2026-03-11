"""
app/services/deduplication.py
─────────────────────────────
Deduplication service — runs inside Celery workers.
Three-pass: DOI exact match → normalised title exact match → TF-IDF title cosine.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Callable, Optional
from uuid import UUID

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sqlalchemy import select
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
    if not title or not isinstance(title, str):
        return ""
    title = unicodedata.normalize("NFKD", title).lower().strip()
    title = title.replace("&", "and")
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
        return {"total": 0, "doi_removed": 0, "title_removed": 0, "tfidf_removed": 0}

    total = len(papers)
    if progress_cb:
        progress_cb(5, f"Deduplicating {total} papers …")

    removed_ids: set = set()

    # ── Pass 1: DOI-based deduplication ────────────────────────────────────────
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
                removed_ids.add(p.id)
                doi_removed += 1
                flog.info("  DOI dup: '%s' (doi=%s) — duplicate of '%s'", p.title, ndoi, best.title)

    db.flush()
    flog.info("--- Pass 1 (DOI) done: %d duplicates ---", doi_removed)

    if progress_cb:
        progress_cb(35, f"DOI pass done ({doi_removed} removed). Title matching…")

    # ── Pass 2: Exact normalised title ─────────────────────────────────────────
    remaining = [p for p in papers if p.id not in removed_ids]
    title_groups: dict[str, list[Paper]] = {}
    for p in remaining:
        nt = _norm_title(p.title)
        if nt:
            title_groups.setdefault(nt, []).append(p)

    title_removed = 0
    for nt, group in title_groups.items():
        if len(group) <= 1:
            continue
        best = max(group, key=_completeness)
        for p in group:
            if p.id != best.id and p.id not in removed_ids:
                p.is_duplicate = True
                p.duplicate_of_id = best.id
                removed_ids.add(p.id)
                title_removed += 1
                flog.info("  Title dup: '%s' — duplicate of '%s'", p.title, best.title)

    db.flush()
    flog.info("--- Pass 2 (title) done: %d duplicates ---", title_removed)

    if progress_cb:
        progress_cb(65, f"Title pass done ({title_removed} removed). TF-IDF matching…")

    # ── Pass 3: TF-IDF title cosine similarity ─────────────────────────────────
    remaining = [p for p in papers if p.id not in removed_ids]
    tfidf_removed = 0

    if len(remaining) >= 2:
        titles = [_norm_title(p.title) for p in remaining]
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 4))
        tfidf = vec.fit_transform(titles)
        sim = cosine_similarity(tfidf)
        np.fill_diagonal(sim, 0)

        for i in range(len(remaining)):
            if remaining[i].id in removed_ids:
                continue
            for j in range(i + 1, len(remaining)):
                if remaining[j].id in removed_ids:
                    continue
                if sim[i, j] >= 0.85:
                    if _completeness(remaining[i]) >= _completeness(remaining[j]):
                        loser = remaining[j]
                        winner = remaining[i]
                    else:
                        loser = remaining[i]
                        winner = remaining[j]
                    loser.is_duplicate = True
                    loser.duplicate_of_id = winner.id
                    removed_ids.add(loser.id)
                    tfidf_removed += 1
                    flog.info("  TF-IDF dup (%.2f): '%s' — duplicate of '%s'",
                              sim[i, j], loser.title, winner.title)
                    if loser.id == remaining[i].id:
                        break

    db.flush()
    flog.info("--- Pass 3 (TF-IDF) done: %d duplicates ---", tfidf_removed)

    total_removed = doi_removed + title_removed + tfidf_removed
    non_dup_count = total - total_removed
    project.total_after_dedup = non_dup_count
    db.commit()

    flog.info("=== Dedup complete — %d unique (removed %d by DOI, %d by title, %d by TF-IDF) ===",
              non_dup_count, doi_removed, title_removed, tfidf_removed)

    if progress_cb:
        progress_cb(
            100,
            f"Dedup complete — {non_dup_count} unique papers "
            f"(removed {doi_removed} DOI + {title_removed} title + {tfidf_removed} TF-IDF)"
        )

    return {
        "total": total,
        "doi_removed": doi_removed,
        "title_removed": title_removed,
        "tfidf_removed": tfidf_removed,
        "remaining": non_dup_count,
    }
