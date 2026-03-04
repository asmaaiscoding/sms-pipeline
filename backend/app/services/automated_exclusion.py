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
You are a strict academic paper screener for a Systematic Mapping Study focused \
exclusively on **SOFTWARE security** — meaning security concerns that arise \
within the SOFTWARE layer (source code, binaries, applications, libraries, \
frameworks, APIs, software development processes).

Task: Given the title (and abstract when available) of an academic paper, decide \
whether it should be INCLUDED or EXCLUDED.

── INCLUDE only if the paper's PRIMARY focus is one of these SOFTWARE-LEVEL topics:
• Vulnerability detection / analysis / classification in source code or binaries
• Software vulnerability remediation, patching, or repair
• Static analysis, dynamic analysis, symbolic execution, or taint analysis for security
• Fuzz testing / fuzzing of software components
• Secure coding practices, security-aware code review, or security refactoring
• Software penetration testing or security auditing of applications
• Malware analysis, reverse engineering of malicious software
• Security of web applications, mobile applications, desktop applications
• API security, authentication / authorization implementation flaws in software
• Software supply-chain security (dependency vulnerabilities, package managers)
• DevSecOps, CI/CD pipeline security, SAST/DAST/IAST tools
• Security-related software metrics, vulnerability prediction models in code
• Cryptographic API misuse or insecure cryptographic implementations in software

── EXCLUDE if the paper's PRIMARY focus is any of the following (even if it \
mentions "security"):
• Network security, firewall rules, intrusion detection/prevention systems (IDS/IPS)
• Hardware security, side-channel attacks on chips, TPM, TEE at hardware level
• IoT security focused on device/hardware/protocol layer, not software
• Cloud infrastructure security, VM isolation (unless it's about software config flaws)
• Cyber-physical systems security (SCADA, power grids) without a software code focus
• Wireless / radio / protocol-level security (Bluetooth, Zigbee, 5G)
• Pure cryptography theory, mathematical proofs, new cipher design
• Information security management, governance, risk frameworks, compliance, policy
• Privacy, GDPR, data protection law / regulations
• Social engineering, phishing awareness, human factors in security
• Digital forensics focused on disk/network/memory forensics (not software-level)
• General software engineering with no security dimension
• Biometrics, physical access control
• Any domain outside computer science (medicine, biology, finance, etc.)

── Decision rules:
1. If the title/abstract is ambiguous, lean toward EXCLUDE — we want high precision.
2. A paper that merely USES software but studies network/hardware/policy security \
is still EXCLUDED.
3. "Security testing" counts as INCLUDE only if it tests the software itself, not \
the network or infrastructure.

Respond with ONLY one word: INCLUDE or EXCLUDE

Title: "{title}"
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
