"""
app/services/pdf_finder.py
──────────────────────────
Find direct PDF links via multiple sources:
  OpenAlex, arXiv, Semantic Scholar, BASE, Unpaywall, CORE, DOI-direct.
Runs synchronously inside Celery workers.

Strict title matching ensures we return the correct PDF, not another paper's.
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
    """Jaccard similarity on word sets. Higher threshold = stricter match."""
    wa, wb = set(_norm(a).split()), set(_norm(b).split())
    if not wa or not wb:
        return False
    return len(wa & wb) / len(wa | wb) >= threshold


def _titles_match_strict(a, b) -> bool:
    """Strict match: normalized titles must be very similar (>= 0.85 Jaccard)."""
    return _titles_match(a, b, threshold=0.85)


def _clean_doi(doi) -> str:
    if not doi:
        return ""
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", str(doi).strip(), flags=re.I)


def _is_pdf(url: str) -> bool:
    """Check if URL actually serves a PDF by reading the first bytes."""
    if not url:
        return False
    try:
        r = _SESSION.get(url, timeout=TIMEOUT, stream=True, headers={"Range": "bytes=0-4"})
        chunk = next(r.iter_content(5), b"")
        r.close()
        return chunk.startswith(b"%PDF")
    except Exception:
        return False


def _is_pdf_url_pattern(url: str) -> bool:
    """Quick check if URL looks like a PDF URL before doing a HEAD request."""
    if not url:
        return False
    url_lower = url.lower()
    return (
        url_lower.endswith(".pdf") or
        "/pdf/" in url_lower or
        "pdf" in url_lower.split("?")[0].split("/")[-1]
    )


# ── Sources ───────────────────────────────────────────────────────────────────

def _try_doi_direct(title: str, doi: str) -> Optional[str]:
    """Try to get PDF directly from the DOI resolver / publisher page."""
    if not doi:
        return None
    try:
        # Many publishers serve PDF at known URL patterns
        doi_url = f"https://doi.org/{doi}"

        # Try Sci-Hub style patterns and publisher direct links
        # Check if the DOI resolves to a page with a PDF link
        r = _SESSION.get(doi_url, timeout=TIMEOUT, allow_redirects=True)
        final_url = r.url

        # arXiv
        if "arxiv.org" in final_url:
            arxiv_id = re.search(r"arxiv\.org/abs/([^\s?#]+)", final_url)
            if arxiv_id:
                pdf = f"https://arxiv.org/pdf/{arxiv_id.group(1)}.pdf"
                if _is_pdf(pdf):
                    return pdf

        # MDPI — open access
        if "mdpi.com" in final_url:
            pdf_url = final_url.rstrip("/") + "/pdf"
            if _is_pdf(pdf_url):
                return pdf_url

        # IEEE — try the PDF stamp URL
        if "ieeexplore.ieee.org" in final_url:
            arnumber = re.search(r"/document/(\d+)", final_url)
            if arnumber:
                pdf_url = f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?arnumber={arnumber.group(1)}"
                if _is_pdf(pdf_url):
                    return pdf_url

        # ACM — try /doi/pdf/ pattern
        if "dl.acm.org" in final_url:
            pdf_url = final_url.replace("/doi/", "/doi/pdf/")
            if _is_pdf(pdf_url):
                return pdf_url

        # SpringerLink — try /content/pdf/ pattern
        if "link.springer.com" in final_url:
            pdf_url = final_url.replace("/article/", "/content/pdf/") + ".pdf"
            if _is_pdf(pdf_url):
                return pdf_url

    except Exception as e:
        logger.debug("DOI direct error: %s", e)
    return None


def _try_openalex(title: str, doi: str) -> Optional[str]:
    try:
        url = f"https://api.openalex.org/works?search={quote(title)}&per_page=5"
        r = _SESSION.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        for w in r.json().get("results", []):
            if not _titles_match_strict(title, w.get("title", "")):
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
                    # Verify title matches
                    tm = re.search(r"<title>(.*?)</title>", entry, re.S)
                    if tm and _titles_match_strict(title, tm.group(1).strip().replace("\n", " ")):
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
                if tm and _titles_match_strict(title, tm.group(1).strip().replace("\n", " ")):
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
            if not _titles_match_strict(title, p.get("title", "")):
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
            if not any(_titles_match_strict(title, t) for t in dtitles):
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


def _try_unpaywall(title: str, doi: str) -> Optional[str]:
    """Unpaywall provides free legal PDF links for papers with DOIs."""
    if not doi:
        return None
    try:
        url = f"https://api.unpaywall.org/v2/{quote(doi)}?email=sms-pipeline@example.org"
        r = _SESSION.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        data = r.json()
        # Verify title
        api_title = data.get("title", "")
        if api_title and not _titles_match_strict(title, api_title):
            return None
        # Check best OA location
        best = data.get("best_oa_location") or {}
        pdf_url = best.get("url_for_pdf", "")
        if pdf_url and _is_pdf(pdf_url):
            return pdf_url
        # Check all OA locations
        for loc in data.get("oa_locations", []):
            pdf_url = loc.get("url_for_pdf", "")
            if pdf_url and _is_pdf(pdf_url):
                return pdf_url
    except Exception as e:
        logger.debug("Unpaywall error: %s", e)
    return None


def _try_core(title: str, doi: str) -> Optional[str]:
    """CORE aggregates open-access research outputs."""
    try:
        url = f"https://api.core.ac.uk/v3/search/works?q={quote(title)}&limit=5"
        r = _SESSION.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        for result in r.json().get("results", []):
            result_title = result.get("title", "")
            if not _titles_match_strict(title, result_title):
                continue
            # Check download URL
            dl_url = result.get("downloadUrl", "")
            if dl_url and _is_pdf(dl_url):
                return dl_url
            # Check links
            for link in result.get("links", []):
                link_url = link.get("url", "") if isinstance(link, dict) else str(link)
                if link_url and _is_pdf(link_url):
                    return link_url
    except Exception as e:
        logger.debug("CORE error: %s", e)
    return None


_PIPELINE = [
    ("DOI-Direct",        _try_doi_direct),
    ("Unpaywall",         _try_unpaywall),
    ("OpenAlex",          _try_openalex),
    ("arXiv",             _try_arxiv),
    ("SemanticScholar",   _try_semantic_scholar),
    ("CORE",              _try_core),
    ("BASE",              _try_base),
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
