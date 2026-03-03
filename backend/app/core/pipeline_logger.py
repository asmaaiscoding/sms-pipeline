"""
app/core/pipeline_logger.py
───────────────────────────
Creates a dedicated file logger for each pipeline job.
Log files are written to /app/logs/<step>_<timestamp>.log
and contain detailed per-paper information.

The file is flushed immediately on every write so that it is visible
on disk the moment the collection step begins.
"""

from __future__ import annotations

import logging
import os
import platform
import sys
from datetime import datetime, timezone

LOG_DIR = os.environ.get("PIPELINE_LOG_DIR", "/app/logs")


class _FlushHandler(logging.FileHandler):
    """FileHandler that flushes after every emit so the file is always up-to-date."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


def get_pipeline_logger(step: str, project_name: str = "") -> logging.Logger:
    """
    Return a logger that writes to a timestamped file under LOG_DIR.
    e.g. /app/logs/collect_2026-03-02_01-45-00.log

    The log file is created **immediately** (even before the first real
    log message) and every message is flushed to disk right away.
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

    # ── file handler (auto-flush) ─────────────────────────────────────────
    fh = _FlushHandler(filepath, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # ── also stream to stderr so celery worker output shows these ─────────
    sh = logging.StreamHandler(sys.stderr)
    sh.setLevel(logging.DEBUG)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # ── header ────────────────────────────────────────────────────────────
    logger.info("=" * 70)
    logger.info("PIPELINE STEP: %s", step.upper())
    logger.info("Project: %s", project_name or "(unknown)")
    logger.info("Started: %s UTC", ts)
    logger.info("Log file: %s", filepath)
    logger.info("Python: %s", sys.version)
    logger.info("Platform: %s", platform.platform())
    logger.info("PID: %d", os.getpid())
    logger.info("=" * 70)

    return logger
