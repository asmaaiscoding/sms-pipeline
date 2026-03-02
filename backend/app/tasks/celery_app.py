"""
app/tasks/celery_app.py
───────────────────────
Celery application factory and configuration.
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "sms_pipeline",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    # Serialisation
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # Task routing
    task_routes={
        "app.tasks.pipeline.run_pipeline_step": {"queue": "scraping"},
    },

    # Reliability
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,

    # Result expiry
    result_expires=86400,  # 24h

    # Concurrency — scraping is IO-bound, keep it low
    worker_concurrency=2,

    # Periodic tasks
    beat_schedule={
        "cleanup-stale-jobs": {
            "task": "app.tasks.pipeline.cleanup_stale_jobs",
            "schedule": crontab(minute="*/30"),
        },
    },
)

# Explicitly import tasks so @shared_task decorators register with this app
import app.tasks.pipeline  # noqa: F401, E402
