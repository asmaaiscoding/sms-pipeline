"""
steps/find_links.py
-------------------
Step 4 — Find a direct PDF link for every paper.

Sources tried (in order):
    1. OpenAlex
    2. arXiv
    3. Semantic Scholar
    4. BASE (Bielefeld Academic Search Engine)

Each candidate URL is verified by reading the first bytes and checking for
the ``%PDF`` magic signature.

Usage (stand-alone):
    python -m steps.find_links input.xlsx output.xlsx
"""

from __future__ import annotations

import os
import re
import sys
import time
from typing import Optional, Tuple
from urllib.parse import quote

import pandas as pd
import requests

from config import PDF_SEARCH_TIMEOUT, PDF_SEARCH_SLEEP, OUTPUT_DIR


# ═══════════════════════════════════════════════════════════════════════════════
#  HTTP session
# ═══════════════════════════════════════════════════════════════════════════════

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
})


# ═══════════════════════════════════════════════════════════════════════════════
#  Title helpers
# ═══════════════════════════════════════════════════════════════════════════════

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
    if not doi or pd.isna(doi):
        return ""
    return re.sub(r"^https?://(dx\.)?doi\.org/", "", str(doi).strip(), flags=re.I)


# ═══════════════════════════════════════════════════════════════════════════════
#  PDF verification
# ═══════════════════════════════════════════════════════════════════════════════

def _is_pdf(url: str) -> bool:
    if not url:
        return False
    try:
        r = _SESSION.get(url, timeout=PDF_SEARCH_TIMEOUT, stream=True,
                         headers={"Range": "bytes=0-4"})
        chunk = next(r.iter_content(5), b"")
        r.close()
        return chunk.startswith(b"%PDF")
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
#  Source 1 — OpenAlex
# ═══════════════════════════════════════════════════════════════════════════════

def _try_openalex(title: str, doi: str) -> Optional[str]:
    try:
        url = f"https://api.openalex.org/works?search={quote(title)}&per_page=5"
        r = _SESSION.get(url, timeout=PDF_SEARCH_TIMEOUT)
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
        print(f"      OpenAlex error: {e}")
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Source 2 — arXiv
# ═══════════════════════════════════════════════════════════════════════════════

def _try_arxiv(title: str, doi: str) -> Optional[str]:
    try:
        # by DOI
        if doi:
            r = _SESSION.get(
                f"http://export.arxiv.org/api/query?search_query=doi:{quote(doi)}&max_results=3",
                timeout=PDF_SEARCH_TIMEOUT,
            )
            if r.status_code == 200:
                for entry in re.findall(r"<entry>(.*?)</entry>", r.text, re.S):
                    m = re.search(r"<id>http://arxiv\.org/abs/([^<]+)</id>", entry)
                    if m:
                        pdf = f"https://arxiv.org/pdf/{m.group(1)}.pdf"
                        if _is_pdf(pdf):
                            return pdf
            time.sleep(PDF_SEARCH_SLEEP)

        # by title
        r = _SESSION.get(
            f"http://export.arxiv.org/api/query?search_query=ti:{quote(title)}&max_results=3",
            timeout=PDF_SEARCH_TIMEOUT,
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
        print(f"      arXiv error: {e}")
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Source 3 — Semantic Scholar
# ═══════════════════════════════════════════════════════════════════════════════

def _try_semantic_scholar(title: str, doi: str) -> Optional[str]:
    try:
        url = (
            "https://api.semanticscholar.org/graph/v1/paper/search"
            f"?query={quote(title)}&fields=title,openAccessPdf,externalIds&limit=5"
        )
        r = _SESSION.get(url, timeout=PDF_SEARCH_TIMEOUT)
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
        print(f"      SemanticScholar error: {e}")
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Source 4 — BASE
# ═══════════════════════════════════════════════════════════════════════════════

def _try_base(title: str, doi: str) -> Optional[str]:
    try:
        query = f'dcdoi:"{doi}"' if doi else f'dctitle:"{title}"'
        r = _SESSION.get(
            "https://api.base-search.net/cgi-bin/BaseHttpSearchInterface.fcgi",
            params={"func": "PerformSearch", "query": query, "format": "json", "hits": 5},
            timeout=PDF_SEARCH_TIMEOUT,
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
        print(f"      BASE error: {e}")
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  Dispatcher
# ═══════════════════════════════════════════════════════════════════════════════

_PIPELINE = [
    ("OpenAlex",         _try_openalex),
    ("arXiv",            _try_arxiv),
    ("SemanticScholar",  _try_semantic_scholar),
    ("BASE",             _try_base),
]


def _search_pdf(title, doi=None) -> Tuple[Optional[str], Optional[str]]:
    if not title or pd.isna(title):
        return None, None
    title = str(title).strip()
    doi = _clean_doi(doi)

    for name, fn in _PIPELINE:
        try:
            url = fn(title, doi)
            if url:
                print(f"    -> [{name}] {url[:90]}")
                return url, name
        except Exception:
            pass
        time.sleep(PDF_SEARCH_SLEEP)

    print("    -> Not found")
    return None, None


# ═══════════════════════════════════════════════════════════════════════════════
#  Main entry point
# ═══════════════════════════════════════════════════════════════════════════════

def run_link_finder(
    input_path: str,
    output_path: Optional[str] = None,
    save_every: int = 50,
) -> str:
    """Add PDF_Link and PDF_Source columns to *input_path*.  Returns output path."""
    if output_path is None:
        output_path = os.path.join(OUTPUT_DIR, "final_with_links.xlsx")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    df = pd.read_excel(input_path)
    total = len(df)

    print(f"\n{'=' * 70}")
    print("  PDF LINK FINDER — OpenAlex | arXiv | SemanticScholar | BASE")
    print(f"{'=' * 70}")
    print(f"  {total} papers to process\n")

    if "PDF_Link"   not in df.columns:
        df["PDF_Link"]   = None
    if "PDF_Source" not in df.columns:
        df["PDF_Source"] = None

    found = 0
    stats: dict = {}

    for i, row in df.iterrows():
        # Skip if already has a verified link
        if pd.notna(row.get("PDF_Link")) and str(row["PDF_Link"]).startswith("http"):
            found += 1
            continue

        title = row.get("Title")
        doi   = row.get("DOI")
        print(f"  [{i + 1}/{total}] {str(title)[:70]}")

        url, source = _search_pdf(title, doi)
        df.at[i, "PDF_Link"]  = url
        df.at[i, "PDF_Source"] = source

        if url:
            found += 1
            stats[source] = stats.get(source, 0) + 1

        if (i + 1) % save_every == 0:
            df.to_excel(output_path, index=False, engine="openpyxl")
            print(f"  [checkpoint] saved ({i + 1}/{total})")

    df.to_excel(output_path, index=False, engine="openpyxl")

    print(f"\n{'=' * 70}")
    pct = f"{found / total * 100:.1f}" if total else "0"
    print(f"  PDFs found: {found}/{total} ({pct}%)")
    if stats:
        print("  By source:")
        for src, cnt in sorted(stats.items(), key=lambda x: -x[1]):
            print(f"    {src:<20} {cnt}")
    print(f"  Output → {output_path}")
    print(f"{'=' * 70}")
    return output_path


# ── stand-alone ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python -m steps.find_links input.xlsx [output.xlsx]")
    out = sys.argv[2] if len(sys.argv) >= 3 else None
    run_link_finder(sys.argv[1], out)
