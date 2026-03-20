"""
app/api/projects.py
───────────────────
CRUD endpoints for SMS projects.
"""

from __future__ import annotations

import math
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import PaginationParams, get_current_user
from app.config import get_settings
from app.core.exceptions import ForbiddenError, NotFoundError
from app.database import get_db
from app.models.project import Project, ProjectStatus, SearchString, TopicEnum
from app.models.user import User
from app.schemas.project import (
    PaginatedProjects,
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
)

router = APIRouter(prefix="/projects", tags=["Projects"])
settings = get_settings()


async def _get_project_or_404(
    project_id: UUID,
    user: User,
    db: AsyncSession,
) -> Project:
    result = await db.execute(
        select(Project)
        .options(selectinload(Project.search_strings))
        .where(Project.id == project_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise NotFoundError("Project not found")
    if project.owner_id != user.id and not user.is_superuser:
        raise ForbiddenError("Not your project")
    return project


@router.get("", response_model=PaginatedProjects)
async def list_projects(
    pagination: PaginationParams = Depends(),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all projects for the current user."""
    base_q = select(Project).where(Project.owner_id == user.id)

    total_result = await db.execute(select(func.count()).select_from(base_q.subquery()))
    total = total_result.scalar() or 0

    result = await db.execute(
        base_q.order_by(Project.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.per_page)
    )
    projects = result.scalars().all()

    return PaginatedProjects(
        items=[ProjectListResponse.model_validate(p) for p in projects],
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=math.ceil(total / pagination.per_page) if total else 0,
    )


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new SMS project with search strings."""
    project = Project(
        owner_id=user.id,
        name=body.name,
        description=body.description,
        topic=body.topic,
        year_start=body.year_start,
        year_end=body.year_end,
        sources=",".join(body.sources),
        status=ProjectStatus.DRAFT,
    )
    db.add(project)
    await db.flush()

    # Add search strings — either preset or custom
    if body.use_preset_strings and body.topic != TopicEnum.CUSTOM:
        preset = (
            settings.detection_strings()
            if body.topic == TopicEnum.DETECTION
            else settings.remediation_strings()
        )
        for label, query in preset.items():
            db.add(SearchString(project_id=project.id, label=label, query=query))
    elif body.search_strings:
        for ss in body.search_strings:
            db.add(SearchString(project_id=project.id, label=ss.label, query=ss.query))

    await db.flush()
    await db.refresh(project)

    # Re-fetch with relationships
    return await _get_project_or_404(project.id, user, db)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await _get_project_or_404(project_id, user, db)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    body: ProjectUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(project_id, user, db)

    update_data = body.model_dump(exclude_unset=True)
    if "sources" in update_data:
        update_data["sources"] = ",".join(update_data["sources"])

    for field, val in update_data.items():
        setattr(project, field, val)

    await db.flush()
    await db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Lightweight ownership check without loading relationships
    result = await db.execute(
        select(Project.id, Project.owner_id).where(Project.id == project_id)
    )
    row = result.first()
    if not row:
        raise NotFoundError("Project not found")
    if row.owner_id != user.id and not user.is_superuser:
        raise ForbiddenError("Not your project")

    # Raw SQL delete — DB CASCADE handles children
    await db.execute(
        text("DELETE FROM projects WHERE id = :pid"),
        {"pid": str(project_id)},
    )
    await db.commit()
