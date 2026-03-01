"""
app/schemas/job.py
──────────────────
Pydantic schemas for Job tracking.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel

from app.models.job import JobStatus, JobType


class JobCreate(BaseModel):
    job_type: JobType
    config: Optional[Dict[str, Any]] = None


class JobResponse(BaseModel):
    id: UUID
    project_id: UUID
    job_type: JobType
    status: JobStatus
    progress: float
    message: str
    config: Optional[Dict[str, Any]]
    result: Optional[Dict[str, Any]]
    error_message: str
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class PaginatedJobs(BaseModel):
    items: List[JobResponse]
    total: int
    page: int
    per_page: int
    pages: int


class JobProgressUpdate(BaseModel):
    """Sent via WebSocket to the frontend."""
    job_id: str
    project_id: str
    status: JobStatus
    progress: float
    message: str
