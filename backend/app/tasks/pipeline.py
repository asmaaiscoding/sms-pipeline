"""
app/tasks/pipeline.py
─────────────────────
Celery tasks that execute each pipeline step.
Uses synchronous SQLAlchemy session (Celery workers are sync).
Progress is published to Redis for WebSocket relay.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import UUID

import redis
from celery import shared_task
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session, sessionmaker

# IMPORTANT: import celery_app first so the app (with routing config)
# is created before @shared_task decorators bind to it.
from app.tasks.celery_app import celery_app  # noqa: F401
from app.config import get_settings
from app.models.job import Job, JobStatus
from app.models.project import Project, ProjectStatus

logger = logging.getLogger(__name__)
settings = get_settings()

# Sync engine for Celery workers
_sync_engine = create_engine(
    settings.DATABASE_URL_SYNC,
    pool_size=5,
    max_overflow=5,
    pool_pre_ping=True,
)
SyncSession = sessionmaker(bind=_sync_engine)

# Redis client for progress pub/sub
_redis = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

_STEP_MAP = {
    "collect": ("app.services.collection", "run_collection_sync", ProjectStatus.COLLECTING),
    "deduplicate": ("app.services.deduplication", "run_deduplication_sync", ProjectStatus.DEDUPLICATING),
    "filter": ("app.services.filtering", "run_filtering_sync", ProjectStatus.FILTERING),
    "find_links": ("app.services.pdf_finder", "run_pdf_finder_sync", ProjectStatus.FINDING_LINKS),
}

_FULL_PIPELINE_ORDER = ["collect", "deduplicate", "find_links"]


class JobCancelled(Exception):
    """Raised when a job is cancelled via Redis flag."""
    pass


def _publish_progress(job_id: str, project_id: str, status: str, progress: float, message: str):
    """Publish progress update to Redis pub/sub channel."""
    payload = json.dumps({
        "job_id": job_id,
        "project_id": project_id,
        "status": status,
        "progress": round(progress, 1),
        "message": message,
    })
    _redis.publish(f"job:{project_id}", payload)

    # Also store latest state in a key (for late-joining WebSocket clients)
    _redis.setex(f"job_progress:{job_id}", 3600, payload)


def _check_cancelled(job_id: str):
    """Check if this job has been cancelled via Redis flag."""
    if _redis.get(f"job_cancel:{job_id}"):
        raise JobCancelled(f"Job {job_id} was cancelled")


def _make_progress_cb(job_id: str, project_id: str, db: Session):
    """Return a callback that updates both DB and Redis, and checks for cancellation."""
    def cb(progress: float, message: str):
        _check_cancelled(job_id)
        db.execute(
            update(Job)
            .where(Job.id == UUID(job_id))
            .values(progress=progress, message=message)
        )
        db.commit()
        _publish_progress(job_id, project_id, "running", progress, message)
    return cb


@shared_task(name="app.tasks.pipeline.run_pipeline_step", bind=True, max_retries=1)
def run_pipeline_step(self, job_id: str, project_id: str, step_type: str):
    """Execute a single pipeline step or the full pipeline."""
    db = SyncSession()

    try:
        # Mark job as running
        job = db.execute(select(Job).where(Job.id == UUID(job_id))).scalar_one()
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(timezone.utc)
        db.commit()

        _publish_progress(job_id, project_id, "running", 0, f"Starting {step_type}…")

        steps_to_run = _FULL_PIPELINE_ORDER if step_type == "full_pipeline" else [step_type]

        combined_result = {}

        for i, step in enumerate(steps_to_run):
            if step not in _STEP_MAP:
                raise ValueError(f"Unknown step: {step}")

            module_path, func_name, proj_status = _STEP_MAP[step]

            # Update project status
            db.execute(
                update(Project)
                .where(Project.id == UUID(project_id))
                .values(status=proj_status)
            )
            db.commit()

            # Import and run the service function
            import importlib
            mod = importlib.import_module(module_path)
            func = getattr(mod, func_name)

            # For multi-step, scale progress per step
            if len(steps_to_run) > 1:
                base_pct = (i / len(steps_to_run)) * 100
                step_scale = 100 / len(steps_to_run)

                def scaled_cb(progress, message, _base=base_pct, _scale=step_scale):
                    total_progress = _base + (progress / 100) * _scale
                    _make_progress_cb(job_id, project_id, db)(total_progress, message)

                result = func(db, UUID(project_id), progress_cb=scaled_cb)
            else:
                result = func(db, UUID(project_id), progress_cb=_make_progress_cb(job_id, project_id, db))

            combined_result[step] = result

        # Mark job as completed
        job = db.execute(select(Job).where(Job.id == UUID(job_id))).scalar_one()
        job.status = JobStatus.COMPLETED
        job.progress = 100
        job.message = "Completed successfully"
        job.result = combined_result
        job.completed_at = datetime.now(timezone.utc)

        # Update project status
        db.execute(
            update(Project)
            .where(Project.id == UUID(project_id))
            .values(status=ProjectStatus.COMPLETED)
        )
        db.commit()

        _publish_progress(job_id, project_id, "completed", 100, "Completed successfully")
        logger.info("Job %s completed", job_id)

    except JobCancelled:
        logger.info("Job %s was cancelled", job_id)
        db.rollback()
        try:
            job = db.execute(select(Job).where(Job.id == UUID(job_id))).scalar_one()
            if job.status != JobStatus.CANCELLED:
                job.status = JobStatus.CANCELLED
                job.message = "Cancelled by user"
                job.completed_at = datetime.now(timezone.utc)
            db.execute(
                update(Project)
                .where(Project.id == UUID(project_id))
                .values(status=ProjectStatus.DRAFT)
            )
            db.commit()
        except Exception:
            logger.exception("Failed to update cancelled job status")
        _publish_progress(job_id, project_id, "cancelled", 0, "Cancelled by user")

    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        db.rollback()

        try:
            job = db.execute(select(Job).where(Job.id == UUID(job_id))).scalar_one()
            job.status = JobStatus.FAILED
            job.error_message = str(exc)[:2000]
            job.completed_at = datetime.now(timezone.utc)

            db.execute(
                update(Project)
                .where(Project.id == UUID(project_id))
                .values(status=ProjectStatus.FAILED)
            )
            db.commit()
        except Exception:
            logger.exception("Failed to update job status")

        _publish_progress(job_id, project_id, "failed", 0, str(exc)[:500])
        raise

    finally:
        db.close()


@shared_task(name="app.tasks.pipeline.cleanup_stale_jobs")
def cleanup_stale_jobs():
    """Mark jobs stuck in RUNNING for > 6 hours or PENDING for > 10 min as FAILED."""
    db = SyncSession()
    try:
        from datetime import timedelta
        cutoff_running = datetime.now(timezone.utc) - timedelta(hours=6)
        cutoff_pending = datetime.now(timezone.utc) - timedelta(minutes=10)

        stale = db.execute(
            select(Job).where(
                Job.status == JobStatus.RUNNING,
                Job.started_at < cutoff_running,
            )
        ).scalars().all()

        stale_pending = db.execute(
            select(Job).where(
                Job.status == JobStatus.PENDING,
                Job.created_at < cutoff_pending,
            )
        ).scalars().all()

        for job in stale:
            job.status = JobStatus.FAILED
            job.error_message = "Job timed out (>6h)"
            job.completed_at = datetime.now(timezone.utc)
            logger.warning("Marked stale running job %s as failed", job.id)

        for job in stale_pending:
            job.status = JobStatus.FAILED
            job.error_message = "Job never started (stale pending)"
            job.completed_at = datetime.now(timezone.utc)
            logger.warning("Marked stale pending job %s as failed", job.id)

        db.commit()

    except Exception:
        db.rollback()
        logger.exception("cleanup_stale_jobs failed")
    finally:
        db.close()
