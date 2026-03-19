"""
app/services/automated_exclusion.py
────────────────────────────────────
Automated exclusion using local Ollama (llama3.2:3b).

Sends paper title + abstract to the LLM and asks for an INCLUDE/EXCLUDE
decision based on software security relevance.
"""

from __future__ import annotations

import logging
import time
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

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:3b"

EXCLUSION_PROMPT = """\
You are a strict academic paper screener for a Systematic Mapping Study focused \
on SOFTWARE security at the software artifact level (source code, binaries, \
applications, libraries, frameworks, APIs, and software pipelines).

Task: Given the title and abstract of an academic paper, decide \
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
1. If ambiguous but there is explicit evidence of software artifact + vulnerability \
or software-security testing, prefer INCLUDE.
2. If ambiguity remains and software artifact evidence is missing, EXCLUDE.
3. A paper that merely USES software but studies network/hardware/policy security \
is still EXCLUDED.
4. "Security testing" counts as INCLUDE only if it tests the software artifact itself, \
not just network or infrastructure.

Respond with ONLY one word: INCLUDE or EXCLUDE

Title: "{title}"
Abstract: "{abstract}"
"""


def _call_ollama(title: str, abstract: str = "", max_retries: int = 3) -> str:
    """Call local Ollama and return 'INCLUDE' or 'EXCLUDE'."""
    prompt = EXCLUSION_PROMPT.format(
        title=title,
        abstract=abstract if abstract else "Not available",
    )
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.0,
            "num_predict": 10,
        },
    }

    for attempt in range(max_retries):
        try:
            resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
            resp.raise_for_status()
            answer = resp.json().get("response", "").strip().upper()

            if "INCLUDE" in answer:
                return "INCLUDE"
            elif "EXCLUDE" in answer:
                return "EXCLUDE"
            else:
                logger.warning("Unexpected Ollama response for '%s': %s", title[:60], answer)
                return "INCLUDE"

        except requests.exceptions.RequestException as e:
            logger.warning("Ollama API error (attempt %d): %s", attempt + 1, e)
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                continue
            return "INCLUDE"  # On failure, default to keeping the paper

    return "INCLUDE"


def run_automated_exclusion_sync(
    db: Session,
    project_id: UUID,
    progress_cb: Optional[Callable[[float, str], None]] = None,
) -> dict:
    """
    Automated exclusion step — uses local Ollama to classify papers.
    """
    project = db.execute(
        select(Project).where(Project.id == project_id)
    ).scalar_one()

    flog = get_pipeline_logger("automated_exclusion", project.name)
    flog.info("=== Automated exclusion started for project '%s' ===", project.name)

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

    flog.info("Processing %d papers with Ollama (%s)", total, OLLAMA_MODEL)
    if progress_cb:
        progress_cb(5, f"Processing {total} papers with Ollama ({OLLAMA_MODEL})…")

    # Process papers sequentially (local model, no need for parallel batching)
    included = 0
    excluded = 0

    for idx, paper in enumerate(papers):
        abstract = getattr(paper, "abstract", None) or ""
        decision = _call_ollama(paper.title, abstract)

        if decision == "INCLUDE":
            paper.relevance = RelevanceDecision.KEEP
            included += 1
        else:
            paper.relevance = RelevanceDecision.REMOVE
            excluded += 1

        flog.info("  #%d [%s] '%s'", idx + 1, decision, paper.title[:80])

        if progress_cb and (idx + 1) % 10 == 0:
            pct = min((idx + 1) / total * 95 + 5, 99)
            progress_cb(pct, f"Processed {idx + 1}/{total} papers…")

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
