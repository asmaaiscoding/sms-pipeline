"""
app/services/collection.py
──────────────────────────
Paper-collection orchestrator executed inside Celery workers.
Drives the scrapers and stores results in the database.
"""

from __future__ import annotations

import logging
import traceback
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

    # --- file logger (created IMMEDIATELY — flushes every line) ---
    flog = get_pipeline_logger("collection", project.name)
    flog.info("=" * 70)
    flog.info("COLLECTION STARTED")
    flog.info("  Project name : %s", project.name)
    flog.info("  Project ID   : %s", project_id)
    flog.info("=" * 70)

    search_strings_rows = db.execute(
        select(SearchString).where(SearchString.project_id == project_id)
    ).scalars().all()

    search_strings: Dict[str, str] = {ss.label: ss.query for ss in search_strings_rows}
    sources = project.sources_list

    flog.info("Configuration:")
    flog.info("  Sources          : %s", ", ".join(s.upper() for s in sources) if sources else "(none)")
    flog.info("  Search strings   : %d", len(search_strings))
    for label, query in search_strings.items():
        flog.info("    [%s] → %s", label, query)
    flog.info("  Year range       : %d – %d", project.year_start, project.year_end)
    flog.info("  Total year span  : %d years", project.year_end - project.year_start + 1)

    if not search_strings:
        logger.warning("No search strings for project %s", project_id)
        flog.warning("No search strings configured — aborting collection")
        return {"error": "No search strings configured"}

    if not sources:
        flog.warning("No sources configured — aborting collection")
        return {"error": "No sources configured"}

    total_tasks = len(sources) * len(search_strings) * (project.year_end - project.year_start + 1)
    flog.info("Estimated task count: %d (sources × strings × years)", total_tasks)
    completed_tasks = 0
    stats: dict = {}

    flog.info("--- Setting up Selenium WebDriver ---")
    try:
        driver = setup_driver(headless=True, flog=flog)
        flog.info("WebDriver created successfully")
    except Exception as exc:
        flog.error("FAILED to create WebDriver: %s", exc, exc_info=True)
        raise

    try:
        for src_idx, src in enumerate(sources):
            scraper_path = _SCRAPER_MAP.get(src)
            if not scraper_path:
                logger.warning("Unknown source %s — skipping", src)
                flog.warning("Unknown source '%s' — skipping (available: %s)", src, list(_SCRAPER_MAP.keys()))
                continue

            flog.info("")
            flog.info("=" * 60)
            flog.info("[%d/%d] SOURCE: %s", src_idx + 1, len(sources), src.upper())
            flog.info("  Scraper class: %s", scraper_path)
            flog.info("=" * 60)

            try:
                ScraperCls = _import_scraper(scraper_path)
                flog.debug("  Scraper class imported: %s", ScraperCls)
            except Exception as exc:
                flog.error("  FAILED to import scraper %s: %s", scraper_path, exc, exc_info=True)
                continue

            try:
                scraper = ScraperCls(driver, flog=flog)
                flog.debug("  Scraper instance created")
            except Exception as exc:
                flog.error("  FAILED to instantiate scraper: %s", exc, exc_info=True)
                continue

            src_label = src.upper()
            src_pct_base = (src_idx / len(sources)) * 100
            src_pct_scale = 100 / len(sources)

            if progress_cb:
                progress_cb(
                    src_pct_base,
                    f"[{src_idx + 1}/{len(sources)}] Starting {src_label}…"
                )

            flog.info("  Calling scraper.scrape() with %d search strings, years %d–%d",
                       len(search_strings), project.year_start, project.year_end)

            try:
                df = scraper.scrape(search_strings, project.year_start, project.year_end)
                flog.info("  scraper.scrape() returned DataFrame with %d rows, columns=%s",
                           len(df), list(df.columns) if not df.empty else "[]")
            except Exception as exc:
                flog.error("  scraper.scrape() RAISED EXCEPTION: %s", exc, exc_info=True)
                flog.error("  Full traceback:\n%s", traceback.format_exc())
                stats[src] = 0
                continue

            count = 0

            for row_idx, row in df.iterrows():
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
                flog.debug("  saved #%d  %s  (year=%s, doi=%s, link=%s)",
                           count, title[:80], year or "?", doi or "—",
                           str(row.get("Link", ""))[:100])

            try:
                db.flush()
                flog.info("  DB flush OK — %d papers from %s", count, src_label)
            except Exception as exc:
                flog.error("  DB flush FAILED for %s: %s", src_label, exc, exc_info=True)
                raise

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
        flog.info("")
        flog.info("=" * 70)
        sources_summary = ", ".join(f"{k.upper()}: {v}" for k, v in stats.items())
        flog.info("COLLECTION COMPLETE — %d papers (%s)", total, sources_summary)
        flog.info("=" * 70)

        if progress_cb:
            progress_cb(100, f"Collection complete — {total} papers ({sources_summary})")

        return stats

    except Exception as exc:
        flog.error("")
        flog.error("=" * 70)
        flog.error("COLLECTION FAILED: %s", exc)
        flog.error("Full traceback:\n%s", traceback.format_exc())
        flog.error("=" * 70)
        db.rollback()
        raise
    finally:
        flog.info("Shutting down WebDriver...")
        try:
            driver.quit()
            flog.info("WebDriver closed.")
        except Exception as exc:
            flog.warning("WebDriver quit error (non-fatal): %s", exc)


def _safe_int(val) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None
