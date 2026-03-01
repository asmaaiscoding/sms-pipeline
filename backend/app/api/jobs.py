"""
app/api/jobs.py
───────────────
Job management — launch pipeline steps, check status, cancel.
"""

from __future__ import annotations

import math
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PaginationParams, get_current_user
from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.database import get_db
from app.models.job import Job, JobStatus, JobType
from app.models.project import Project
from app.models.user import User
from app.schemas.job import JobCreate, JobResponse, PaginatedJobs
from app.tasks.pipeline import run_pipeline_step

router = APIRouter(prefix="/projects/{project_id}/jobs", tags=["Jobs"])


async def _get_project(project_id: UUID, user: User, db: AsyncSession) -> Project:
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise NotFoundError("Project not found")
    if project.owner_id != user.id and not user.is_superuser:
        raise ForbiddenError("Not your project")
    return project


@router.get("", response_model=PaginatedJobs)
async def list_jobs(
    project_id: UUID,
    pagination: PaginationParams = Depends(),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_project(project_id, user, db)

    base_q = select(Job).where(Job.project_id == project_id)

    total = (await db.execute(
        select(func.count()).select_from(base_q.subquery())
    )).scalar() or 0

    result = await db.execute(
        base_q.order_by(Job.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.per_page)
    )
    jobs = result.scalars().all()

    return PaginatedJobs(
        items=[JobResponse.model_validate(j) for j in jobs],
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=math.ceil(total / pagination.per_page) if total else 0,
    )


@router.post("", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    project_id: UUID,
    body: JobCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Launch a new pipeline step as a background job."""
    project = await _get_project(project_id, user, db)

    # Prevent duplicate running jobs of the same type
    running = await db.execute(
        select(Job).where(
            Job.project_id == project_id,
            Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
        )
    )
    if running.scalars().first():
        raise BadRequestError("A job is already running for this project")

    job = Job(
        project_id=project_id,
        job_type=body.job_type,
        status=JobStatus.PENDING,
        config=body.config or {},
    )
    db.add(job)
    await db.flush()
    await db.refresh(job)

    # Dispatch to Celery
    task = run_pipeline_step.delay(str(job.id), str(project_id), body.job_type.value)
    job.celery_task_id = task.id
    await db.flush()

    return job


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(
    project_id: UUID,
    job_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_project(project_id, user, db)

    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.project_id == project_id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise NotFoundError("Job not found")
    return job


@router.post("/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    project_id: UUID,
    job_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a pending or running job."""
    await _get_project(project_id, user, db)

    result = await db.execute(
        select(Job).where(Job.id == job_id, Job.project_id == project_id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise NotFoundError("Job not found")
    if job.status not in (JobStatus.PENDING, JobStatus.RUNNING):
        raise BadRequestError("Job cannot be cancelled")

    # Revoke Celery task
    if job.celery_task_id:
        from app.tasks.celery_app import celery_app
        celery_app.control.revoke(job.celery_task_id, terminate=True)

    job.status = JobStatus.CANCELLED
    job.message = "Cancelled by user"
    await db.flush()
    await db.refresh(job)
    return job
