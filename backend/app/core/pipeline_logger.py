"""
app/core/pipeline_logger.py
───────────────────────────
Creates a dedicated file logger for each pipeline job.
Log files are written to /app/logs/<step>_<timestamp>.log
and contain detailed per-paper information.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

LOG_DIR = os.environ.get("PIPELINE_LOG_DIR", "/app/logs")


def get_pipeline_logger(step: str, project_name: str = "") -> logging.Logger:
    """
    Return a logger that writes to a timestamped file under LOG_DIR.
    e.g. /app/logs/collect_2026-03-02_01-45-00.log
    """
    os.makedirs(LOG_DIR, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    safe_name = project_name.replace(" ", "_")[:30] if project_name else "job"
    filename = f"{step}_{safe_name}_{ts}.log"
    filepath = os.path.join(LOG_DIR, filename)

    logger = logging.getLogger(f"pipeline.{step}.{ts}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False  # don't duplicate to root

    # Remove existing handlers if any
    logger.handlers.clear()

    fh = logging.FileHandler(filepath, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    logger.info("=" * 70)
    logger.info("PIPELINE STEP: %s", step.upper())
    logger.info("Project: %s", project_name or "(unknown)")
    logger.info("Started: %s UTC", ts)
    logger.info("Log file: %s", filepath)
    logger.info("=" * 70)

    return logger
