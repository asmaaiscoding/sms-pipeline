"""
app/scrapers/base.py
────────────────────
Shared Selenium utilities used by every source-specific scraper.
Supports headless mode for production Celery workers.
"""

from __future__ import annotations

import logging
import os
import time
import random
from typing import Any, Callable, Optional

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

logger = logging.getLogger(__name__)

# A no-op logger used when no file logger is provided
_NullLogger: Optional[logging.Logger] = None


def _get_null_logger() -> logging.Logger:
    global _NullLogger
    if _NullLogger is None:
        _NullLogger = logging.getLogger("scrapers.null")
        _NullLogger.addHandler(logging.NullHandler())
        _NullLogger.propagate = False
    return _NullLogger


# ═══════════════════════════════════════════════════════════════════════════════
#  Driver setup
# ═══════════════════════════════════════════════════════════════════════════════

def setup_driver(headless: bool = True, flog: Optional[logging.Logger] = None) -> webdriver.Chrome:
    """Return a Chrome WebDriver with anti-detection tweaks."""
    fl = flog or _get_null_logger()
    fl.info("[DRIVER] Setting up Chrome WebDriver (headless=%s)", headless)

    opts = Options()

    if headless:
        opts.add_argument("--headless=new")

    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-extensions")
    opts.add_argument("--disable-infobars")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    # Use system chromedriver if available (Docker)
    chromedriver_path = os.environ.get("CHROMEDRIVER_PATH")
    chrome_bin = os.environ.get("CHROME_BIN")

    fl.debug("[DRIVER] CHROMEDRIVER_PATH=%s", chromedriver_path or "(not set)")
    fl.debug("[DRIVER] CHROME_BIN=%s", chrome_bin or "(not set)")

    if chrome_bin:
        opts.binary_location = chrome_bin

    service_kwargs = {}
    if chromedriver_path:
        service_kwargs["executable_path"] = chromedriver_path

    try:
        driver = webdriver.Chrome(
            options=opts,
            service=Service(**service_kwargs) if service_kwargs else Service(),
        )
        fl.info("[DRIVER] Chrome WebDriver created successfully")
    except Exception as exc:
        fl.error("[DRIVER] FAILED to create Chrome WebDriver: %s", exc, exc_info=True)
        raise

    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
        )
        fl.debug("[DRIVER] Anti-detection CDP command injected")
    except Exception as exc:
        fl.warning("[DRIVER] CDP command failed (non-fatal): %s", exc)

    driver.set_page_load_timeout(60)
    fl.debug("[DRIVER] Page load timeout set to 60s")
    fl.info("[DRIVER] WebDriver ready")
    return driver


# ═══════════════════════════════════════════════════════════════════════════════
#  Delay & retry
# ═══════════════════════════════════════════════════════════════════════════════

def random_delay(lo: float = 2.0, hi: float = 5.0) -> None:
    time.sleep(random.uniform(lo, hi))


def retry(func: Callable, retries: int = 3, *args: Any, flog: Optional[logging.Logger] = None, **kwargs: Any) -> Any:
    """Call *func* with exponential back-off on failure."""
    fl = flog or _get_null_logger()
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            if attempt < retries - 1:
                wait = (2 ** attempt) + random.uniform(0, 1)
                logger.warning("Retry %d/%d: %s", attempt + 1, retries, exc)
                fl.warning("[RETRY] Attempt %d/%d failed: %s — waiting %.1fs", attempt + 1, retries, exc, wait)
                time.sleep(wait)
            else:
                fl.error("[RETRY] All %d attempts exhausted. Last error: %s", retries, exc, exc_info=True)
                raise


# ═══════════════════════════════════════════════════════════════════════════════
#  CAPTCHA helpers
# ═══════════════════════════════════════════════════════════════════════════════

# Keywords that strongly indicate a CAPTCHA/block page (matched against page title + visible text)
_BLOCK_TITLE_KEYWORDS = [
    "just a moment",
    "access denied",
    "attention required",
    "request rejected",
]

# Keywords matched against the visible body text (not page source) to avoid
# false positives from embedded reCAPTCHA scripts in working pages
_BLOCK_BODY_KEYWORDS = [
    "verify you are human",
    "please verify",
    "unusual traffic",
    "performing security verification",
    "challenge-running",
]


def has_captcha(driver: webdriver.Chrome, flog: Optional[logging.Logger] = None) -> bool:
    """Heuristic: return True if the page looks like a CAPTCHA / block page.

    Uses page title + visible body text (not page source) to avoid false
    positives from embedded reCAPTCHA JavaScript on legitimate result pages.
    """
    fl = flog or _get_null_logger()
    try:
        title = (driver.title or "").lower()
        current_url = driver.current_url
        fl.debug("[CAPTCHA-CHECK] URL=%s  title='%s'", current_url, title[:120])

        if any(kw in title for kw in _BLOCK_TITLE_KEYWORDS):
            matched = [kw for kw in _BLOCK_TITLE_KEYWORDS if kw in title]
            fl.warning("[CAPTCHA-CHECK] Title matched block keywords: %s", matched)
            return True

        # Check visible body text for block indicators
        try:
            body_text = driver.find_element(
                webdriver.common.by.By.TAG_NAME, "body"
            ).text.lower()
        except Exception as exc:
            fl.debug("[CAPTCHA-CHECK] Could not read body text: %s", exc)
            body_text = ""

        fl.debug("[CAPTCHA-CHECK] Body text length: %d chars", len(body_text))

        if any(kw in body_text for kw in _BLOCK_BODY_KEYWORDS):
            matched = [kw for kw in _BLOCK_BODY_KEYWORDS if kw in body_text]
            fl.warning("[CAPTCHA-CHECK] Body text matched block keywords: %s", matched)
            return True

        # If the page has very little visible text AND mentions captcha/cloudflare
        # in the source, it's likely a block page
        if len(body_text) < 200:
            src = driver.page_source.lower()
            fl.debug("[CAPTCHA-CHECK] Short page (%d chars body) — checking source (%d chars) for captcha markers",
                     len(body_text), len(src))
            captcha_markers = ["cf-browser-verification", "challenge-platform", "captcha", "hcaptcha"]
            if any(kw in src for kw in captcha_markers):
                matched = [kw for kw in captcha_markers if kw in src]
                fl.warning("[CAPTCHA-CHECK] Short page + source markers: %s → likely blocked", matched)
                return True

        fl.debug("[CAPTCHA-CHECK] No CAPTCHA detected")
        return False
    except Exception as exc:
        fl.debug("[CAPTCHA-CHECK] Exception during check: %s", exc)
        return False


def ensure_page_ready(driver: webdriver.Chrome, first_visit: bool, flog: Optional[logging.Logger] = None) -> None:
    """In headless mode, log warnings instead of blocking for human input."""
    fl = flog or _get_null_logger()
    if has_captcha(driver, flog=fl):
        logger.warning("CAPTCHA detected — page may be blocked")
        fl.warning("[PAGE-READY] CAPTCHA detected — page may be blocked. URL=%s", driver.current_url)
    elif first_visit:
        logger.info("First visit — page loaded")
        fl.info("[PAGE-READY] First visit — page loaded successfully. URL=%s", driver.current_url)
