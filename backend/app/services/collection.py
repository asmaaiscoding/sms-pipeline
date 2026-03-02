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
from app.core.pipeline_logger import get_pipeline_logger

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

    # --- file logger ---
    flog = get_pipeline_logger("collection", project.name)
    flog.info("=== Collection started for project '%s' (id=%s) ===", project.name, project_id)

    search_strings_rows = db.execute(
        select(SearchString).where(SearchString.project_id == project_id)
    ).scalars().all()

    search_strings: Dict[str, str] = {ss.label: ss.query for ss in search_strings_rows}
    sources = project.sources_list

    flog.info("Sources: %s", ", ".join(s.upper() for s in sources))
    flog.info("Search strings: %s", search_strings)
    flog.info("Year range: %d – %d", project.year_start, project.year_end)

    if not search_strings:
        logger.warning("No search strings for project %s", project_id)
        flog.warning("No search strings configured — aborting")
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
                flog.warning("Unknown source '%s' — skipping", src)
                continue

            ScraperCls = _import_scraper(scraper_path)
            scraper = ScraperCls(driver)

            src_label = src.upper()
            src_pct_base = (src_idx / len(sources)) * 100
            src_pct_scale = 100 / len(sources)

            flog.info("--- [%d/%d] Starting %s ---", src_idx + 1, len(sources), src_label)

            if progress_cb:
                progress_cb(
                    src_pct_base,
                    f"[{src_idx + 1}/{len(sources)}] Starting {src_label}…"
                )

            df = scraper.scrape(search_strings, project.year_start, project.year_end)
            count = 0

            for _, row in df.iterrows():
                title = str(row.get("Title", ""))
                year = _safe_int(row.get("Year"))
                doi = str(row.get("DOI", ""))

                paper = Paper(
                    project_id=project_id,
                    title=title,
                    authors=str(row.get("Authors", "")),
                    year=year,
                    publisher=str(row.get("Publisher", "")),
                    doi=doi,
                    link=str(row.get("Link", "")),
                    source=src,
                    search_string_label=str(row.get("Search_String", "")),
                )
                db.add(paper)
                count += 1
                flog.info("  #%d  %s  (year=%s, doi=%s)", count, title, year or "?", doi or "—")

            db.flush()
            stats[src] = count

            flog.info("--- %s finished: %d papers ---", src_label, count)

            # Progress: source done
            collected_so_far = sum(stats.values())
            done_pct = src_pct_base + src_pct_scale
            if progress_cb:
                progress_cb(
                    min(done_pct, 99),
                    f"[{src_idx + 1}/{len(sources)}] {src_label} done — "
                    f"{count} papers collected (total so far: {collected_so_far})"
                )

            logger.info("Collected %d papers from %s", count, src)

        # Update project stats
        total = sum(stats.values())
        project.total_collected = total

        db.commit()

        sources_summary = ", ".join(f"{k.upper()}: {v}" for k, v in stats.items())
        flog.info("=== Collection complete — %d papers (%s) ===", total, sources_summary)

        if progress_cb:
            progress_cb(100, f"Collection complete — {total} papers ({sources_summary})")

        return stats

    except Exception as exc:
        flog.error("Collection FAILED: %s", exc, exc_info=True)
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
