"""
app/services/collection.py
──────────────────────────
Paper-collection orchestrator executed inside Celery workers.
Drives the scrapers and stores results in the database.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.paper import Paper
from app.models.project import Project, SearchString
from app.scrapers.base import setup_driver

logger = logging.getLogger(__name__)

# Lazy imports to avoid loading scrapers at module level
_SCRAPER_MAP = {
    "acm": "app.scrapers.acm.ACMScraper",
    "ieee": "app.scrapers.ieee.IEEEScraper",
    "springer": "app.scrapers.springer.SpringerScraper",
}


def _import_scraper(path: str):
    module_path, cls_name = path.rsplit(".", 1)
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, cls_name)


def run_collection_sync(
    db: Session,
    project_id: UUID,
    progress_cb: Optional[Callable[[float, str], None]] = None,
) -> dict:
    """
    Synchronous collection — meant to run inside a Celery worker.

    Returns a dict with {source: count} stats.
    """
    project = db.execute(
        select(Project).where(Project.id == project_id)
    ).scalar_one()

    search_strings_rows = db.execute(
        select(SearchString).where(SearchString.project_id == project_id)
    ).scalars().all()

    search_strings: Dict[str, str] = {ss.label: ss.query for ss in search_strings_rows}
    sources = project.sources_list

    if not search_strings:
        logger.warning("No search strings for project %s", project_id)
        return {"error": "No search strings configured"}

    total_tasks = len(sources) * len(search_strings) * (project.year_end - project.year_start + 1)
    completed_tasks = 0
    stats: dict = {}

    driver = setup_driver(headless=True)

    try:
        for src_idx, src in enumerate(sources):
            scraper_path = _SCRAPER_MAP.get(src)
            if not scraper_path:
                logger.warning("Unknown source %s — skipping", src)
                continue

            ScraperCls = _import_scraper(scraper_path)
            scraper = ScraperCls(driver)

            if progress_cb:
                progress_cb(
                    (src_idx / len(sources)) * 100,
                    f"Collecting from {src.upper()}…"
                )

            df = scraper.scrape(search_strings, project.year_start, project.year_end)
            count = 0

            for _, row in df.iterrows():
                paper = Paper(
                    project_id=project_id,
                    title=str(row.get("Title", "")),
                    authors=str(row.get("Authors", "")),
                    year=_safe_int(row.get("Year")),
                    publisher=str(row.get("Publisher", "")),
                    doi=str(row.get("DOI", "")),
                    link=str(row.get("Link", "")),
                    source=src,
                    search_string_label=str(row.get("Search_String", "")),
                )
                db.add(paper)
                count += 1

            db.flush()
            stats[src] = count
            logger.info("Collected %d papers from %s", count, src)

        # Update project stats
        total = sum(stats.values())
        project.total_collected = total

        db.commit()

        if progress_cb:
            progress_cb(100, f"Collection complete — {total} papers")

        return stats

    except Exception:
        db.rollback()
        raise
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def _safe_int(val) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None
