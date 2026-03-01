"""
app/api/exports.py
──────────────────
Export papers as xlsx / csv.
"""

from __future__ import annotations

import io
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import pandas as pd

from app.api.deps import get_current_user
from app.core.exceptions import ForbiddenError, NotFoundError
from app.database import get_db
from app.models.paper import Paper, RelevanceDecision
from app.models.project import Project
from app.models.user import User

router = APIRouter(prefix="/projects/{project_id}/export", tags=["Export"])


@router.get("")
async def export_papers(
    project_id: UUID,
    format: str = Query("xlsx", regex="^(xlsx|csv)$"),
    include_duplicates: bool = Query(False),
    relevance_filter: str = Query("all", regex="^(all|keep|remove|pending)$"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Download project papers as xlsx or csv."""
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise NotFoundError("Project not found")
    if project.owner_id != user.id and not user.is_superuser:
        raise ForbiddenError()

    q = select(Paper).where(Paper.project_id == project_id)

    if not include_duplicates:
        q = q.where(Paper.is_duplicate == False)  # noqa: E712

    if relevance_filter != "all":
        mapping = {
            "keep": RelevanceDecision.KEEP,
            "remove": RelevanceDecision.REMOVE,
            "pending": RelevanceDecision.PENDING,
        }
        q = q.where(Paper.relevance == mapping[relevance_filter])

    q = q.order_by(Paper.year.desc(), Paper.title)
    papers_result = await db.execute(q)
    papers = papers_result.scalars().all()

    # Build DataFrame
    rows = []
    for p in papers:
        rows.append({
            "Title": p.title,
            "Authors": p.authors,
            "Year": p.year,
            "Publisher": p.publisher,
            "DOI": p.doi,
            "Link": p.link,
            "Source": p.source,
            "Search_String": p.search_string_label,
            "Relevance": p.relevance.value if p.relevance else "",
            "PDF_Link": p.pdf_link,
            "PDF_Source": p.pdf_source,
        })

    df = pd.DataFrame(rows)
    buf = io.BytesIO()

    filename = f"{project.name.replace(' ', '_')}_papers"

    if format == "xlsx":
        df.to_excel(buf, index=False, engine="openpyxl")
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}.xlsx"'},
        )
    else:
        df.to_csv(buf, index=False)
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
        )
