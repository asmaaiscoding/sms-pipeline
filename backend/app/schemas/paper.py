"""
app/schemas/paper.py
────────────────────
Pydantic schemas for Paper CRUD and search.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.paper import RelevanceDecision


class PaperResponse(BaseModel):
    id: UUID
    title: str
    authors: str
    year: Optional[int]
    publisher: str
    doi: str
    link: str
    abstract: str
    source: str
    search_string_label: str
    is_duplicate: bool
    relevance: RelevanceDecision
    pdf_link: str
    pdf_source: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PaperBriefResponse(BaseModel):
    id: UUID
    title: str
    authors: str
    year: Optional[int]
    source: str
    is_duplicate: bool
    relevance: RelevanceDecision
    pdf_link: str

    model_config = {"from_attributes": True}


class PaginatedPapers(BaseModel):
    items: List[PaperBriefResponse]
    total: int
    page: int
    per_page: int
    pages: int


class PaperUpdate(BaseModel):
    relevance: Optional[RelevanceDecision] = None
    pdf_link: Optional[str] = None


class PaperFilters(BaseModel):
    source: Optional[str] = None
    year: Optional[int] = None
    relevance: Optional[RelevanceDecision] = None
    is_duplicate: Optional[bool] = None
    has_pdf: Optional[bool] = None
    search: Optional[str] = None


class PaperStats(BaseModel):
    total: int
    by_source: dict[str, int]
    by_year: dict[int, int]
    by_relevance: dict[str, int]
    duplicates: int
    with_pdf: int
