"""
app/api/papers.py
─────────────────
Paper listing, detail, stats, file import, and manual updates.
"""

from __future__ import annotations

import math
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, UploadFile, File
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import PaginationParams, get_current_user
from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.database import get_db
from app.models.paper import Paper, RelevanceDecision
from app.models.project import Project
from app.models.user import User
from app.schemas.paper import (
    PaginatedPapers,
    PaperBriefResponse,
    PaperResponse,
    PaperStats,
    PaperUpdate,
)

router = APIRouter(prefix="/projects/{project_id}/papers", tags=["Papers"])


async def _verify_project_access(project_id: UUID, user: User, db: AsyncSession):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise NotFoundError("Project not found")
    if project.owner_id != user.id and not user.is_superuser:
        raise ForbiddenError("Not your project")
    return project


@router.get("", response_model=PaginatedPapers)
async def list_papers(
    project_id: UUID,
    pagination: PaginationParams = Depends(),
    source: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    relevance: Optional[RelevanceDecision] = Query(None),
    is_duplicate: Optional[bool] = Query(None),
    has_pdf: Optional[bool] = Query(None),
    search: Optional[str] = Query(None, max_length=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List papers with filtering and pagination."""
    await _verify_project_access(project_id, user, db)

    q = select(Paper).where(Paper.project_id == project_id)

    # Apply filters
    if source:
        q = q.where(Paper.source == source)
    if year is not None:
        q = q.where(Paper.year == year)
    if relevance is not None:
        q = q.where(Paper.relevance == relevance)
    if is_duplicate is not None:
        q = q.where(Paper.is_duplicate == is_duplicate)
    if has_pdf is True:
        q = q.where(Paper.pdf_link != "")
    elif has_pdf is False:
        q = q.where(Paper.pdf_link == "")
    if search:
        q = q.where(Paper.title.ilike(f"%{search}%"))

    # Count
    count_q = select(func.count()).select_from(q.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # Fetch page
    result = await db.execute(
        q.order_by(Paper.year.desc(), Paper.title)
        .offset(pagination.offset)
        .limit(pagination.per_page)
    )
    papers = result.scalars().all()

    return PaginatedPapers(
        items=[PaperBriefResponse.model_validate(p) for p in papers],
        total=total,
        page=pagination.page,
        per_page=pagination.per_page,
        pages=math.ceil(total / pagination.per_page) if total else 0,
    )


@router.get("/stats", response_model=PaperStats)
async def get_paper_stats(
    project_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return aggregate statistics for papers in this project."""
    await _verify_project_access(project_id, user, db)

    base = select(Paper).where(Paper.project_id == project_id)

    # Total
    total = (await db.execute(
        select(func.count()).select_from(base.subquery())
    )).scalar() or 0

    # By source
    source_q = await db.execute(
        select(Paper.source, func.count())
        .where(Paper.project_id == project_id)
        .group_by(Paper.source)
    )
    by_source = {row[0] or "unknown": row[1] for row in source_q.all()}

    # By year
    year_q = await db.execute(
        select(Paper.year, func.count())
        .where(Paper.project_id == project_id)
        .where(Paper.year.isnot(None))
        .group_by(Paper.year)
        .order_by(Paper.year)
    )
    by_year = {row[0]: row[1] for row in year_q.all()}

    # By relevance
    rel_q = await db.execute(
        select(Paper.relevance, func.count())
        .where(Paper.project_id == project_id)
        .group_by(Paper.relevance)
    )
    by_relevance = {row[0].value: row[1] for row in rel_q.all()}

    # Duplicates
    dup_count = (await db.execute(
        select(func.count())
        .where(Paper.project_id == project_id)
        .where(Paper.is_duplicate == True)  # noqa: E712
    )).scalar() or 0

    # With PDF
    pdf_count = (await db.execute(
        select(func.count())
        .where(Paper.project_id == project_id)
        .where(Paper.pdf_link != "")
    )).scalar() or 0

    return PaperStats(
        total=total,
        by_source=by_source,
        by_year=by_year,
        by_relevance=by_relevance,
        duplicates=dup_count,
        with_pdf=pdf_count,
    )


@router.get("/{paper_id}", response_model=PaperResponse)
async def get_paper(
    project_id: UUID,
    paper_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _verify_project_access(project_id, user, db)

    result = await db.execute(
        select(Paper).where(Paper.id == paper_id, Paper.project_id == project_id)
    )
    paper = result.scalar_one_or_none()
    if not paper:
        raise NotFoundError("Paper not found")
    return paper


@router.patch("/{paper_id}", response_model=PaperResponse)
async def update_paper(
    project_id: UUID,
    paper_id: UUID,
    body: PaperUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Manually update a paper's relevance or PDF link."""
    await _verify_project_access(project_id, user, db)

    result = await db.execute(
        select(Paper).where(Paper.id == paper_id, Paper.project_id == project_id)
    )
    paper = result.scalar_one_or_none()
    if not paper:
        raise NotFoundError("Paper not found")

    for field, val in body.model_dump(exclude_unset=True).items():
        setattr(paper, field, val)

    await db.flush()
    await db.refresh(paper)
    return paper


@router.get("/filtering-prompt", response_model=dict)
async def get_filtering_prompt(
    project_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the prompt the user should paste into Claude / ChatGPT."""
    await _verify_project_access(project_id, user, db)

    from app.services.filtering import FILTERING_PROMPT

    # Count non-duplicate papers
    total = (await db.execute(
        select(func.count())
        .where(Paper.project_id == project_id)
        .where(Paper.is_duplicate == False)  # noqa: E712
    )).scalar() or 0

    return {
        "prompt": FILTERING_PROMPT,
        "total_papers": total,
        "instructions": (
            "1. Click 'Download XLSX' to export your papers.\n"
            "2. Open Claude (claude.ai) or ChatGPT.\n"
            "3. Upload the XLSX file and paste the prompt below.\n"
            "4. Claude/ChatGPT will mark each paper as KEEP or REMOVE.\n"
            "5. Come back here and update each paper's relevance, "
            "or use the bulk import below."
        ),
    }


@router.post("/import-relevance")
async def import_relevance(
    project_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    decisions: list[dict] = [],
):
    """
    Bulk import KEEP/REMOVE decisions.
    Body: [{"title": "...", "relevance": "keep"}, ...]
    """
    project = await _verify_project_access(project_id, user, db)

    updated = 0
    for item in decisions:
        title = item.get("title", "").strip()
        rel = item.get("relevance", "").strip().lower()
        if not title or rel not in ("keep", "remove"):
            continue

        result = await db.execute(
            select(Paper).where(
                Paper.project_id == project_id,
                Paper.title.ilike(title),
            )
        )
        paper = result.scalar_one_or_none()
        if paper:
            paper.relevance = (
                RelevanceDecision.KEEP if rel == "keep" else RelevanceDecision.REMOVE
            )
            updated += 1

    await db.commit()
    return {"updated": updated, "total_submitted": len(decisions)}


@router.post("/upload")
async def upload_papers_file(
    project_id: UUID,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a CSV or XLSX file to import papers into the project.
    Columns are automatically mapped and missing ones are filled with defaults.
    """
    project = await _verify_project_access(project_id, user, db)

    if not file.filename:
        raise BadRequestError("No file provided")

    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ("csv", "xlsx", "xls"):
        raise BadRequestError("Unsupported file format. Use .csv or .xlsx")

    content = await file.read()
    if len(content) == 0:
        raise BadRequestError("Empty file")

    # Run import in a sync context (pandas is sync)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as SyncSession, sessionmaker
    from app.config import get_settings
    from app.services.file_import import import_file_to_project

    settings = get_settings()
    sync_engine = create_engine(settings.DATABASE_URL_SYNC)
    SyncSessionFactory = sessionmaker(bind=sync_engine)
    sync_db = SyncSessionFactory()

    try:
        result = import_file_to_project(sync_db, project_id, content, file.filename)
    except ValueError as e:
        raise BadRequestError(str(e))
    except Exception as e:
        sync_db.rollback()
        raise BadRequestError(f"Failed to import file: {str(e)}")
    finally:
        sync_db.close()
        sync_engine.dispose()

    # Refresh async session to see new data
    await db.execute(select(Project).where(Project.id == project_id))

    return result
