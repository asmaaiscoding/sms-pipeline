"""
app/models/project.py
─────────────────────
Project and SearchString models.
A Project represents a single SMS study (e.g. "Vulnerability Detection 2000-2026").
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class TopicEnum(str, enum.Enum):
    DETECTION = "detection"
    REMEDIATION = "remediation"
    CUSTOM = "custom"


class ProjectStatus(str, enum.Enum):
    DRAFT = "draft"
    COLLECTING = "collecting"
    DEDUPLICATING = "deduplicating"
    FILTERING = "filtering"
    EXCLUDING = "excluding"
    FINDING_LINKS = "finding_links"
    COMPLETED = "completed"
    FAILED = "failed"


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    topic: Mapped[TopicEnum] = mapped_column(
        Enum(TopicEnum, values_callable=lambda e: [x.value for x in e]),
        default=TopicEnum.DETECTION
    )
    year_start: Mapped[int] = mapped_column(Integer, default=2000)
    year_end: Mapped[int] = mapped_column(Integer, default=2026)
    sources: Mapped[str] = mapped_column(
        String(512), default="acm,ieee,springer,elsevier,wiley",
        comment="Comma-separated list of sources"
    )
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus, values_callable=lambda e: [x.value for x in e]),
        default=ProjectStatus.DRAFT
    )

    # Aggregate stats (updated after each step)
    total_collected: Mapped[int] = mapped_column(Integer, default=0)
    total_after_dedup: Mapped[int] = mapped_column(Integer, default=0)
    total_after_filter: Mapped[int] = mapped_column(Integer, default=0)
    total_with_pdf: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    owner = relationship("User", back_populates="projects")
    search_strings = relationship(
        "SearchString", back_populates="project", cascade="all, delete-orphan",
        passive_deletes=True, order_by="SearchString.label"
    )
    papers = relationship(
        "Paper", back_populates="project", cascade="all, delete-orphan",
        passive_deletes=True
    )
    jobs = relationship(
        "Job", back_populates="project", cascade="all, delete-orphan",
        passive_deletes=True, order_by="Job.created_at.desc()"
    )

    @property
    def sources_list(self) -> list[str]:
        return [s.strip() for s in (self.sources or "").split(",") if s.strip()]

    def __repr__(self) -> str:
        return f"<Project {self.name!r} [{self.status.value}]>"


class SearchString(Base):
    __tablename__ = "search_strings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    project = relationship("Project", back_populates="search_strings")

    def __repr__(self) -> str:
        return f"<SearchString {self.label}: {self.query[:50]}>"
