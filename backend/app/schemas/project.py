"""
app/schemas/project.py
──────────────────────
Pydantic schemas for Project and SearchString CRUD.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.project import ProjectStatus, TopicEnum


# ── SearchString ──────────────────────────────────────────────────────────────

class SearchStringCreate(BaseModel):
    label: str = Field(max_length=64)
    query: str = Field(min_length=5)


class SearchStringResponse(BaseModel):
    id: UUID
    label: str
    query: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Project ───────────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    description: str = ""
    topic: TopicEnum = TopicEnum.DETECTION
    year_start: int = Field(default=2000, ge=1900, le=2100)
    year_end: int = Field(default=2026, ge=1900, le=2100)
    sources: List[str] = Field(default=["acm", "ieee", "springer"])
    search_strings: Optional[List[SearchStringCreate]] = None
    use_preset_strings: bool = True


class ProjectUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=512)
    description: Optional[str] = None
    year_start: Optional[int] = Field(None, ge=1900, le=2100)
    year_end: Optional[int] = Field(None, ge=1900, le=2100)
    sources: Optional[List[str]] = None


class ProjectResponse(BaseModel):
    id: UUID
    name: str
    description: str
    topic: TopicEnum
    year_start: int
    year_end: int
    sources: str
    status: ProjectStatus
    total_collected: int
    total_after_dedup: int
    total_after_filter: int
    total_with_pdf: int
    search_strings: List[SearchStringResponse] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProjectListResponse(BaseModel):
    id: UUID
    name: str
    topic: TopicEnum
    status: ProjectStatus
    total_collected: int
    total_after_dedup: int
    total_after_filter: int
    total_with_pdf: int
    created_at: datetime

    model_config = {"from_attributes": True}


class PaginatedProjects(BaseModel):
    items: List[ProjectListResponse]
    total: int
    page: int
    per_page: int
    pages: int
