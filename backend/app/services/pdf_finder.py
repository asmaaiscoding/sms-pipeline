"""
app/services/pdf_finder.py
──────────────────────────
Find direct PDF links via OpenAlex, arXiv, Semantic Scholar, and BASE.
Runs synchronously inside Celery workers.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Callable, Optional, Tuple
from urllib.parse import quote
from uuid import UUID

import pandas as pd
import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.paper import Paper, RelevanceDecision
from app.models.project import Project
from app.core.pipeline_logger import get_pipeline_logger

logger = logging.getLogger(__name__)
settings = get_settings()

# ── HTTP session ──────────────────────────────────────────────────────────────

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
})

TIMEOUT = settings.PDF_SEARCH_TIMEOUT
SLEEP = settings.PDF_SEARCH_SLEEP


# ── Helpers ───────────────────────────────────────────────────────────────────

def _norm(title) -> str:
    if not title or pd.isna(title):
        return ""
    t = re.sub(r"[^\w\s]", " ", str(title).lower())
    return re.sub(r"\s+", " ", t).strip()


def _titles_match(a, b, threshold: float = 0.75) -> bool:
    wa, wb = set(_norm(a).split()), set(_norm(b).split())
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= threshold


def _clean_doi(doi) -> str:
    if not doi:
        return ""
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", str(doi).strip(), flags=re.I)


def _is_pdf(url: str) -> bool:
    if not url:
        return False
    try:
        r = _SESSION.get(url, timeout=TIMEOUT, stream=True, headers={"Range": "bytes=0-4"})
        chunk = next(r.iter_content(5), b"")
        r.close()
        return chunk.startswith(b"%PDF")
    except Exception:
        return False


# ── Sources ───────────────────────────────────────────────────────────────────

def _try_openalex(title: str, doi: str) -> Optional[str]:
    try:
        url = f"https://api.openalex.org/works?search={quote(title)}&per_page=5"
        r = _SESSION.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        for w in r.json().get("results", []):
            if not _titles_match(title, w.get("title", "")):
                continue
            if doi:
                wdoi = (w.get("doi") or "").replace("https://doi.org/", "")
                if wdoi and wdoi.lower() != doi.lower():
                    continue
            pdf = (w.get("best_oa_location") or {}).get("pdf_url", "")
            if not pdf:
                pdf = (w.get("open_access") or {}).get("oa_url", "")
            if pdf and _is_pdf(pdf):
                return pdf
    except Exception as e:
        logger.debug("OpenAlex error: %s", e)
    return None


def _try_arxiv(title: str, doi: str) -> Optional[str]:
    try:
        if doi:
            r = _SESSION.get(
                f"http://export.arxiv.org/api/query?search_query=doi:{quote(doi)}&max_results=3",
                timeout=TIMEOUT,
            )
            if r.status_code == 200:
                for entry in re.findall(r"<entry>(.*?)</entry>", r.text, re.S):
                    m = re.search(r"<id>http://arxiv\.org/abs/([^<]+)</id>", entry)
                    if m:
                        pdf = f"https://arxiv.org/pdf/{m.group(1)}.pdf"
                        if _is_pdf(pdf):
                            return pdf
            time.sleep(SLEEP)

        r = _SESSION.get(
            f"http://export.arxiv.org/api/query?search_query=ti:{quote(title)}&max_results=3",
            timeout=TIMEOUT,
        )
        if r.status_code == 200:
            for entry in re.findall(r"<entry>(.*?)</entry>", r.text, re.S):
                tm = re.search(r"<title>(.*?)</title>", entry, re.S)
                if tm and _titles_match(title, tm.group(1).strip().replace("\n", " ")):
                    im = re.search(r"<id>http://arxiv\.org/abs/([^<]+)</id>", entry)
                    if im:
                        pdf = f"https://arxiv.org/pdf/{im.group(1)}.pdf"
                        if _is_pdf(pdf):
                            return pdf
    except Exception as e:
        logger.debug("arXiv error: %s", e)
    return None


def _try_semantic_scholar(title: str, doi: str) -> Optional[str]:
    try:
        url = (
            "https://api.semanticscholar.org/graph/v1/paper/search"
            f"?query={quote(title)}&fields=title,openAccessPdf,externalIds&limit=5"
        )
        r = _SESSION.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        for p in r.json().get("data", []):
            if not _titles_match(title, p.get("title", "")):
                continue
            if doi:
                pdoi = p.get("externalIds", {}).get("DOI", "")
                if pdoi and pdoi.lower() != doi.lower():
                    continue
            pdf = (p.get("openAccessPdf") or {}).get("url", "")
            if pdf and _is_pdf(pdf):
                return pdf
            arxiv_id = p.get("externalIds", {}).get("ArXiv", "")
            if arxiv_id:
                pdf = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
                if _is_pdf(pdf):
                    return pdf
    except Exception as e:
        logger.debug("Semantic Scholar error: %s", e)
    return None


def _try_base(title: str, doi: str) -> Optional[str]:
    try:
        query = f'dcdoi:"{doi}"' if doi else f'dctitle:"{title}"'
        r = _SESSION.get(
            "https://api.base-search.net/cgi-bin/BaseHttpSearchInterface.fcgi",
            params={"func": "PerformSearch", "query": query, "format": "json", "hits": 5},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return None
        for doc in r.json().get("response", {}).get("docs", []):
            dtitles = doc.get("dctitle") or []
            if isinstance(dtitles, str):
                dtitles = [dtitles]
            if not any(_titles_match(title, t) for t in dtitles):
                continue
            for link in doc.get("dclink") or []:
                if isinstance(link, str) and _is_pdf(link):
                    return link
            for ident in doc.get("dcidentifier") or []:
                if isinstance(ident, str) and ident.startswith("http") and _is_pdf(ident):
                    return ident
    except Exception as e:
        logger.debug("BASE error: %s", e)
    return None


_PIPELINE = [
    ("OpenAlex", _try_openalex),
    ("arXiv", _try_arxiv),
    ("SemanticScholar", _try_semantic_scholar),
    ("BASE", _try_base),
]


def _search_pdf(title: str, doi: str = "") -> Tuple[str, str]:
    doi = _clean_doi(doi)
    for name, fn in _PIPELINE:
        try:
            url = fn(title, doi)
            if url:
                return url, name
        except Exception:
            pass
        time.sleep(SLEEP)
    return "", ""


# ═══════════════════════════════════════════════════════════════════════════════
#  Service entry point
# ═══════════════════════════════════════════════════════════════════════════════

def run_pdf_finder_sync(
    db: Session,
    project_id: UUID,
    progress_cb: Optional[Callable[[float, str], None]] = None,
) -> dict:
    """Find PDF links for kept papers.  Updates in-place."""
    project = db.execute(
        select(Project).where(Project.id == project_id)
    ).scalar_one()

    flog = get_pipeline_logger("pdf_finder", project.name)
    flog.info("=== PDF finder started for project '%s' ===", project.name)

    papers = db.execute(
        select(Paper)
        .where(
            Paper.project_id == project_id,
            Paper.is_duplicate == False,  # noqa: E712
            Paper.relevance == RelevanceDecision.KEEP,
            Paper.pdf_link == "",
        )
        .order_by(Paper.id)
    ).scalars().all()

    if not papers:
        flog.info("No papers need PDF links")
        if progress_cb:
            progress_cb(100, "No papers need PDF links")
        return {"total": 0, "found": 0}

    total = len(papers)
    found = 0
    stats: dict = {}
    save_every = 50

    for i, paper in enumerate(papers):
        if progress_cb and i % 5 == 0:
            pct = (i / total) * 95
            progress_cb(
                pct,
                f"Searching PDFs… {i}/{total} checked, {found} found so far"
            )

        url, source = _search_pdf(paper.title, paper.doi)
        if url:
            paper.pdf_link = url
            paper.pdf_source = source
            found += 1
            stats[source] = stats.get(source, 0) + 1
            flog.info("  #%d FOUND  '%s' — %s (%s)", i + 1, paper.title, source, url)
        else:
            flog.info("  #%d MISS   '%s'", i + 1, paper.title)

        if (i + 1) % save_every == 0:
            db.flush()
            logger.info("PDF checkpoint: %d/%d found (%d/%d)", found, i + 1, found, total)

    # Final save
    project.total_with_pdf = found + db.execute(
        select(Paper)
        .where(
            Paper.project_id == project_id,
            Paper.is_duplicate == False,  # noqa: E712
            Paper.relevance == RelevanceDecision.KEEP,
            Paper.pdf_link != "",
        )
    ).scalars().all().__len__() - found  # existing + newly found, minus double count

    # Simpler: recount
    total_with_pdf = 0
    for p in db.execute(
        select(Paper).where(
            Paper.project_id == project_id,
            Paper.is_duplicate == False,  # noqa: E712
            Paper.relevance == RelevanceDecision.KEEP,
        )
    ).scalars().all():
        if p.pdf_link:
            total_with_pdf += 1

    project.total_with_pdf = total_with_pdf
    db.commit()

    flog.info("=== PDF search complete — %d/%d found (%s) ===", found, total,
             ", ".join(f"{k}: {v}" for k, v in stats.items()) if stats else "none")

    if progress_cb:
        pct_str = f"{found / total * 100:.1f}" if total else "0"
        src_summary = ", ".join(f"{k}: {v}" for k, v in stats.items()) if stats else "none"
        progress_cb(
            100,
            f"PDF search complete — {found}/{total} found ({pct_str}%) — Sources: {src_summary}"
        )

    return {
        "total": total,
        "found": found,
        "by_source": stats,
    }
