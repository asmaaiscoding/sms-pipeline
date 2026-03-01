"""001_initial_schema

Revision ID: 001
Create Date: 2025-01-01 00:00:00.000000

Creates: users, projects, search_strings, papers, jobs
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Users ─────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), unique=True, index=True, nullable=False),
        sa.Column("hashed_password", sa.String(1024), nullable=False),
        sa.Column("full_name", sa.String(256), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("is_superuser", sa.Boolean(), server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── Projects ──────────────────────────────────────────────────────────────
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("description", sa.Text(), server_default=""),
        sa.Column("topic", sa.Enum("detection", "remediation", "custom", name="topicenum"), server_default="detection"),
        sa.Column("year_start", sa.Integer(), server_default="2000"),
        sa.Column("year_end", sa.Integer(), server_default="2026"),
        sa.Column("sources", sa.String(256), server_default="acm,ieee,springer"),
        sa.Column("status", sa.Enum("draft", "collecting", "deduplicating", "filtering", "finding_links", "completed", "failed", name="projectstatus"), server_default="draft"),
        sa.Column("total_collected", sa.Integer(), server_default="0"),
        sa.Column("total_after_dedup", sa.Integer(), server_default="0"),
        sa.Column("total_after_filter", sa.Integer(), server_default="0"),
        sa.Column("total_with_pdf", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── Search Strings ────────────────────────────────────────────────────────
    op.create_table(
        "search_strings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # ── Papers ────────────────────────────────────────────────────────────────
    op.create_table(
        "papers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("authors", sa.Text(), server_default=""),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("publisher", sa.String(256), server_default=""),
        sa.Column("doi", sa.String(512), server_default="", index=True),
        sa.Column("link", sa.Text(), server_default=""),
        sa.Column("abstract", sa.Text(), server_default=""),
        sa.Column("source", sa.String(64), server_default=""),
        sa.Column("search_string_label", sa.String(64), server_default=""),
        sa.Column("is_duplicate", sa.Boolean(), server_default="false"),
        sa.Column("duplicate_of_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("papers.id", ondelete="SET NULL"), nullable=True),
        sa.Column("relevance", sa.Enum("pending", "keep", "remove", name="relevancedecision"), server_default="pending"),
        sa.Column("pdf_link", sa.Text(), server_default=""),
        sa.Column("pdf_source", sa.String(64), server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_index("ix_papers_project_doi", "papers", ["project_id", "doi"])
    op.create_index("ix_papers_project_source", "papers", ["project_id", "source"])

    # ── Jobs ──────────────────────────────────────────────────────────────────
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("job_type", sa.Enum("collect", "deduplicate", "filter", "find_links", "full_pipeline", name="jobtype"), nullable=False),
        sa.Column("status", sa.Enum("pending", "running", "completed", "failed", "cancelled", name="jobstatus"), server_default="pending"),
        sa.Column("progress", sa.Float(), server_default="0"),
        sa.Column("message", sa.Text(), server_default=""),
        sa.Column("celery_task_id", sa.String(256), server_default=""),
        sa.Column("config", postgresql.JSON(), nullable=True),
        sa.Column("result", postgresql.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_table("papers")
    op.drop_table("search_strings")
    op.drop_table("projects")
    op.drop_table("users")

    for name in ("jobstatus", "jobtype", "relevancedecision", "projectstatus", "topicenum"):
        op.execute(f"DROP TYPE IF EXISTS {name}")
