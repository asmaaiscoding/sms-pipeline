"""
app/models/paper.py
───────────────────
Paper model — one row per collected paper.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, Index,
    Integer, String, Text, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class RelevanceDecision(str, enum.Enum):
    PENDING = "pending"
    KEEP = "keep"
    REMOVE = "remove"


class Paper(Base):
    __tablename__ = "papers"
    __table_args__ = (
        Index("ix_papers_project_doi", "project_id", "doi"),
        Index("ix_papers_project_source", "project_id", "source"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Core metadata ─────────────────────────────────────────────────────────
    title: Mapped[str] = mapped_column(Text, nullable=False)
    authors: Mapped[str] = mapped_column(Text, default="")
    year: Mapped[int] = mapped_column(Integer, nullable=True)
    publisher: Mapped[str] = mapped_column(String(256), default="")
    doi: Mapped[str] = mapped_column(String(512), default="", index=True)
    link: Mapped[str] = mapped_column(Text, default="")
    abstract: Mapped[str] = mapped_column(Text, default="")

    # ── Provenance ────────────────────────────────────────────────────────────
    source: Mapped[str] = mapped_column(
        String(64), default="",
        comment="acm | ieee | springer"
    )
    search_string_label: Mapped[str] = mapped_column(String(64), default="")

    # ── Processing flags ──────────────────────────────────────────────────────
    is_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("papers.id", ondelete="SET NULL"),
        nullable=True,
    )
    relevance: Mapped[RelevanceDecision] = mapped_column(
        Enum(RelevanceDecision), default=RelevanceDecision.PENDING
    )

    # ── PDF link ──────────────────────────────────────────────────────────────
    pdf_link: Mapped[str] = mapped_column(Text, default="")
    pdf_source: Mapped[str] = mapped_column(String(64), default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    project = relationship("Project", back_populates="papers")

    def __repr__(self) -> str:
        return f"<Paper {self.title[:50]!r} ({self.year})>"
