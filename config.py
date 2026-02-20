"""
config.py
---------
Central configuration for the SMS Pipeline.
All default search strings, settings, and column definitions live here.
"""

# ─── Standard output columns ─────────────────────────────────────────────────
PAPER_COLUMNS = [
    "Title", "Authors", "Year", "Publisher", "DOI", "Link",
    "Source", "Search_String",
]

# ─── Preset search strings: DETECTION ─────────────────────────────────────────
DETECTION_STRINGS = {
    "DET-1": (
        '("vulnerability" OR "vulnerabilities" OR "security flaw" OR "security weakness") '
        'AND ("detection" OR "identification" OR "prediction" OR "discovery" OR "analysis") '
        'AND ("software" OR "application" OR "source code" OR "program")'
    ),
    "DET-2": (
        '("static analysis" OR "static code analysis" OR "program analysis" OR "SAST") '
        'AND ("vulnerability" OR "security flaw" OR "security weakness") '
        'AND ("software" OR "code" OR "application")'
    ),
    "DET-3": (
        '("vulnerability" OR "security vulnerability") '
        'AND ("machine learning" OR "deep learning" OR "neural network" OR "learning-based" OR "data-driven") '
        'AND ("software" OR "source code" OR "program")'
    ),
    "DET-4": (
        '("vulnerability detection" OR "vulnerability prediction") '
        'AND ("graph" OR "control flow graph" OR "program dependence graph" '
        'OR "code property graph" OR "graph neural network") '
        'AND ("software" OR "source code")'
    ),
    "DET-5": (
        '("vulnerability detection" OR "security vulnerability") '
        'AND ("large language model" OR "LLM" OR "foundation model") '
        'AND ("software" OR "code")'
    ),
}

# ─── Preset search strings: REMEDIATION ───────────────────────────────────────
REMEDIATION_STRINGS = {
    "REM-1": (
        '("vulnerability" OR "vulnerabilities" OR "security flaw" OR "security weakness") '
        'AND ("remediation" OR "mitigation" OR "fix" OR "fixing" OR "repair" '
        'OR "patch" OR "patching") '
        'AND ("software" OR "code" OR "application" OR "source code")'
    ),
    "REM-2": (
        '("security refactoring" OR "secure refactoring" OR "refactoring") '
        'AND ("vulnerability" OR "security") '
        'AND ("software" OR "code" OR "application")'
    ),
    "REM-3": (
        '("automated" OR "automatic" OR "semi-automated") '
        'AND ("vulnerability remediation" OR "vulnerability mitigation" OR "security fix") '
        'AND ("software" OR "application")'
    ),
    "REM-4": (
        '("code transformation" OR "program transformation" OR "security hardening") '
        'AND ("vulnerability" OR "security flaw") '
        'AND ("remediation" OR "mitigation" OR "fix")'
    ),
}

# ─── Default year range ───────────────────────────────────────────────────────
DEFAULT_YEAR_START = 2000
DEFAULT_YEAR_END = 2026

# ─── Scraping delays (seconds) ────────────────────────────────────────────────
DELAY_BETWEEN_PAGES_MIN = 2
DELAY_BETWEEN_PAGES_MAX = 5
DELAY_BETWEEN_SEARCHES = 4
MAX_RETRIES = 3

# ─── PDF link-finder settings ────────────────────────────────────────────────
PDF_SEARCH_TIMEOUT = 15
PDF_SEARCH_SLEEP = 1.0

# ─── Claude filter settings ──────────────────────────────────────────────────
CLAUDE_URL = "https://claude.ai/new"
CLAUDE_BATCH_SIZE = 40        # papers per message

# ─── Title-similarity threshold for deduplication ─────────────────────────────
TITLE_SIM_THRESHOLD = 0.92

# ─── Output directory ────────────────────────────────────────────────────────
OUTPUT_DIR = "output"
