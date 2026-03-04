"""
app/services/file_import.py
────────────────────────────
Import papers from user-uploaded CSV / XLSX files.
Maps arbitrary columns to our Paper schema and fills missing columns.
"""

from __future__ import annotations

import io
import logging
import re
from typing import Optional
from uuid import UUID

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.paper import Paper
from app.models.project import Project

logger = logging.getLogger(__name__)

# ── Column mapping heuristics ─────────────────────────────────────────────────

# Each key is our canonical column name, values are lowercase patterns we look for
_COLUMN_PATTERNS: dict[str, list[str]] = {
    "title":              ["title", "paper title", "article title", "name", "paper name"],
    "authors":            ["author", "authors", "writer", "creators"],
    "year":               ["year", "publication year", "pub year", "date", "publication date"],
    "publisher":          ["publisher", "journal", "venue", "conference", "source title", "publication"],
    "doi":                ["doi", "digital object identifier"],
    "link":               ["link", "url", "uri", "web", "paper url", "article url"],
    "abstract":           ["abstract", "summary", "description"],
    "source":             ["source", "database", "origin", "db"],
    "search_string_label": ["search string", "search_string", "query", "search label"],
}

# Our canonical columns and their defaults
_DEFAULTS: dict[str, object] = {
    "title": "",
    "authors": "",
    "year": None,
    "publisher": "",
    "doi": "",
    "link": "",
    "abstract": "",
    "source": "imported",
    "search_string_label": "",
}


def _match_column(col: str) -> Optional[str]:
    """Try to match an input column name to one of our canonical fields."""
    col_lower = col.strip().lower()
    # Exact match first
    if col_lower in _DEFAULTS:
        return col_lower
    # Pattern matching
    for canonical, patterns in _COLUMN_PATTERNS.items():
        for pat in patterns:
            if pat == col_lower or pat in col_lower or col_lower in pat:
                return canonical
    return None


def _safe_int(val) -> Optional[int]:
    """Try to extract a year from a value."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    # Try to extract 4-digit year
    m = re.search(r"(19|20)\d{2}", s)
    if m:
        return int(m.group(0))
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return None


def normalize_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    Map arbitrary columns to our canonical schema.
    Returns a DataFrame with all required columns filled.
    """
    # Build column mapping
    col_map: dict[str, str] = {}  # original_col -> canonical
    used_canonical: set[str] = set()

    for col in df.columns:
        canonical = _match_column(col)
        if canonical and canonical not in used_canonical:
            col_map[col] = canonical
            used_canonical.add(canonical)

    # Rename matched columns
    df_renamed = df.rename(columns=col_map)

    # Add missing canonical columns with defaults
    for canonical, default in _DEFAULTS.items():
        if canonical not in df_renamed.columns:
            df_renamed[canonical] = default

    # Clean year column
    df_renamed["year"] = df_renamed["year"].apply(_safe_int)

    # Ensure string columns are strings, not NaN
    for col in ["title", "authors", "publisher", "doi", "link", "abstract", "source", "search_string_label"]:
        df_renamed[col] = df_renamed[col].fillna("").astype(str)

    # Drop rows without a title
    df_renamed = df_renamed[df_renamed["title"].str.strip() != ""]

    return df_renamed[list(_DEFAULTS.keys())]


def import_file_to_project(
    db: Session,
    project_id: UUID,
    file_content: bytes,
    filename: str,
) -> dict:
    """
    Parse an uploaded file (CSV or XLSX), normalize columns, and insert
    papers into the project.

    Returns: {"imported": int, "columns_mapped": dict, "columns_unmapped": list}
    """
    # Read file
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext == "csv":
        df = pd.read_csv(io.BytesIO(file_content))
    elif ext in ("xlsx", "xls"):
        df = pd.read_excel(io.BytesIO(file_content))
    else:
        raise ValueError(f"Unsupported file format: .{ext}. Use .csv or .xlsx")

    if df.empty:
        return {"imported": 0, "columns_mapped": {}, "columns_unmapped": []}

    # Track mapping for user info
    original_cols = list(df.columns)
    col_map_info = {}
    unmapped = []
    for col in original_cols:
        canonical = _match_column(col)
        if canonical:
            col_map_info[col] = canonical
        else:
            unmapped.append(col)

    # Normalize
    df_norm = normalize_dataframe(df)

    # Get project
    project = db.execute(
        select(Project).where(Project.id == project_id)
    ).scalar_one()

    # Insert papers
    count = 0
    for _, row in df_norm.iterrows():
        paper = Paper(
            project_id=project_id,
            title=row["title"],
            authors=row["authors"],
            year=row["year"],
            publisher=row["publisher"],
            doi=row["doi"],
            link=row["link"],
            abstract=row["abstract"],
            source=row["source"] if row["source"] else "imported",
            search_string_label=row["search_string_label"],
        )
        db.add(paper)
        count += 1

    # Update project stats
    project.total_collected = count
    db.flush()
    db.commit()

    logger.info("Imported %d papers from %s into project %s", count, filename, project_id)

    return {
        "imported": count,
        "columns_mapped": col_map_info,
        "columns_unmapped": unmapped,
    }
