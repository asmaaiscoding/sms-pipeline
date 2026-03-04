"""
app/services/automated_exclusion.py
────────────────────────────────────
Automated exclusion using Groq API.

Sends paper titles to Groq LLM (llama3) and asks for an INCLUDE/EXCLUDE
decision based on software security relevance.

Uses up to 5 API keys in round-robin to maximise throughput.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional, Tuple
from uuid import UUID

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.paper import Paper, RelevanceDecision
from app.models.project import Project
from app.core.pipeline_logger import get_pipeline_logger

logger = logging.getLogger(__name__)
settings = get_settings()

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"

EXCLUSION_PROMPT = """\
You are an expert in software engineering and software security research.

I will give you the title of an academic paper. Your task is to determine whether \
this paper is relevant to **software security**.

A paper is RELEVANT (INCLUDE) if it focuses on:
- Software vulnerability detection, analysis, testing, or remediation
- Security flaws in source code, applications, or software systems
- Tools, techniques, or methods for finding or fixing security issues in software
- Secure software development practices or security-aware refactoring
- Malware analysis of software artifacts
- Software supply chain security
- Security of APIs, web applications, mobile applications
- Cryptographic implementations in software
- Software fuzzing, penetration testing, or security auditing

A paper is NOT RELEVANT (EXCLUDE) if it focuses on:
- Network security without a software angle (firewalls, intrusion detection at network level)
- Hardware security, IoT physical-layer security
- Pure cryptography theory without software implementation
- General software engineering without any security concern
- Non-software security topics (physical security, policy, governance, privacy law)
- Medical, biological, or unrelated domains

Respond with ONLY one word: INCLUDE or EXCLUDE

Paper title: "{title}"
"""


def _call_groq(api_key: str, title: str, max_retries: int = 3) -> str:
    """Call Groq API and return 'INCLUDE' or 'EXCLUDE'."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "user", "content": EXCLUSION_PROMPT.format(title=title)}
        ],
        "temperature": 0.0,
        "max_tokens": 10,
    }

    for attempt in range(max_retries):
        try:
            resp = requests.post(
                GROQ_CHAT_URL,
                headers=headers,
                json=payload,
                timeout=30,
            )

            if resp.status_code == 429:
                # Rate limited — wait and retry
                wait = min(2 ** attempt * 2, 30)
                logger.warning("Groq rate limited (key ...%s), waiting %ds", api_key[-4:], wait)
                time.sleep(wait)
                continue

            resp.raise_for_status()
            data = resp.json()
            answer = data["choices"][0]["message"]["content"].strip().upper()

            if "INCLUDE" in answer:
                return "INCLUDE"
            elif "EXCLUDE" in answer:
                return "EXCLUDE"
            else:
                logger.warning("Unexpected Groq response for '%s': %s", title[:60], answer)
                return "INCLUDE"  # When in doubt, keep the paper

        except requests.exceptions.RequestException as e:
            logger.warning("Groq API error (attempt %d): %s", attempt + 1, e)
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            return "INCLUDE"  # On failure, default to keeping the paper

    return "INCLUDE"


def _process_batch(
    papers_batch: List[Tuple[int, Paper]],
    api_key: str,
    flog,
) -> List[Tuple[int, str, str]]:
    """Process a batch of papers with one API key. Returns [(index, paper_title, decision)]."""
    results = []
    for idx, paper in papers_batch:
        decision = _call_groq(api_key, paper.title)
        flog.info("  #%d [%s] '%s' (key ...%s)", idx + 1, decision, paper.title[:80], api_key[-4:])
        results.append((idx, paper.title, decision))
        # Small delay to avoid rate limiting
        time.sleep(0.3)
    return results


def run_automated_exclusion_sync(
    db: Session,
    project_id: UUID,
    progress_cb: Optional[Callable[[float, str], None]] = None,
) -> dict:
    """
    Automated exclusion step — uses Groq to classify papers.
    Distributes work across available API keys for speed.
    """
    project = db.execute(
        select(Project).where(Project.id == project_id)
    ).scalar_one()

    flog = get_pipeline_logger("automated_exclusion", project.name)
    flog.info("=== Automated exclusion started for project '%s' ===", project.name)

    api_keys = settings.groq_api_key_list
    if not api_keys:
        error_msg = "No Groq API keys configured. Set GROQ_API_KEYS in .env"
        flog.error(error_msg)
        if progress_cb:
            progress_cb(100, error_msg)
        return {"error": error_msg, "total": 0, "included": 0, "excluded": 0}

    num_keys = len(api_keys)
    flog.info("Using %d Groq API key(s)", num_keys)

    # Get non-duplicate papers that haven't been decided yet
    papers = db.execute(
        select(Paper)
        .where(
            Paper.project_id == project_id,
            Paper.is_duplicate == False,  # noqa: E712
            Paper.relevance == RelevanceDecision.PENDING,
        )
        .order_by(Paper.id)
    ).scalars().all()

    total = len(papers)
    if not papers:
        flog.info("No papers to process")
        if progress_cb:
            progress_cb(100, "No papers to process for exclusion")
        return {"total": 0, "included": 0, "excluded": 0}

    flog.info("Processing %d papers with %d API key(s)", total, num_keys)
    if progress_cb:
        progress_cb(5, f"Processing {total} papers with {num_keys} Groq API key(s)…")

    # Distribute papers across API keys
    indexed_papers = list(enumerate(papers))
    batches: List[List[Tuple[int, Paper]]] = [[] for _ in range(num_keys)]
    for i, (idx, paper) in enumerate(indexed_papers):
        batches[i % num_keys].append((idx, paper))

    # Process in parallel using ThreadPoolExecutor
    all_results: List[Tuple[int, str, str]] = []
    processed = 0

    with ThreadPoolExecutor(max_workers=num_keys) as executor:
        futures = {
            executor.submit(_process_batch, batch, api_keys[i], flog): i
            for i, batch in enumerate(batches)
            if batch  # skip empty batches
        }

        for future in as_completed(futures):
            try:
                batch_results = future.result()
                all_results.extend(batch_results)
                processed += len(batch_results)

                if progress_cb:
                    pct = min((processed / total) * 95 + 5, 99)
                    progress_cb(pct, f"Processed {processed}/{total} papers…")
            except Exception as e:
                logger.exception("Batch processing error: %s", e)
                flog.error("Batch processing error: %s", e)

    # Apply decisions
    included = 0
    excluded = 0
    decision_map: Dict[str, str] = {title: decision for _, title, decision in all_results}

    for paper in papers:
        decision = decision_map.get(paper.title, "INCLUDE")
        if decision == "INCLUDE":
            paper.relevance = RelevanceDecision.KEEP
            included += 1
        else:
            paper.relevance = RelevanceDecision.REMOVE
            excluded += 1

    # Update project stats
    project.total_after_filter = included
    db.commit()

    flog.info("=== Exclusion complete — %d included, %d excluded out of %d ===",
              included, excluded, total)

    if progress_cb:
        progress_cb(
            100,
            f"Automated exclusion complete — {included} included, {excluded} excluded "
            f"out of {total} papers"
        )

    return {
        "total": total,
        "included": included,
        "excluded": excluded,
    }
