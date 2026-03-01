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
from typing import Any, Callable

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  Driver setup
# ═══════════════════════════════════════════════════════════════════════════════

def setup_driver(headless: bool = True) -> webdriver.Chrome:
    """Return a Chrome WebDriver with anti-detection tweaks."""
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

    if chrome_bin:
        opts.binary_location = chrome_bin

    service_kwargs = {}
    if chromedriver_path:
        service_kwargs["executable_path"] = chromedriver_path

    driver = webdriver.Chrome(
        options=opts,
        service=Service(**service_kwargs) if service_kwargs else Service(),
    )
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
    )
    driver.set_page_load_timeout(60)
    return driver


# ═══════════════════════════════════════════════════════════════════════════════
#  Delay & retry
# ═══════════════════════════════════════════════════════════════════════════════

def random_delay(lo: float = 2.0, hi: float = 5.0) -> None:
    time.sleep(random.uniform(lo, hi))


def retry(func: Callable, retries: int = 3, *args: Any, **kwargs: Any) -> Any:
    """Call *func* with exponential back-off on failure."""
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            if attempt < retries - 1:
                wait = (2 ** attempt) + random.uniform(0, 1)
                logger.warning("Retry %d/%d: %s", attempt + 1, retries, exc)
                time.sleep(wait)
            else:
                raise


# ═══════════════════════════════════════════════════════════════════════════════
#  CAPTCHA helpers
# ═══════════════════════════════════════════════════════════════════════════════

_CAPTCHA_KEYWORDS = [
    "captcha", "recaptcha", "hcaptcha",
    "challenge-running", "cf-browser-verification",
    "verify you are human", "security check",
    "please verify", "access denied",
    "unusual traffic",
]


def has_captcha(driver: webdriver.Chrome) -> bool:
    """Heuristic: return True if the page looks like a CAPTCHA / block page."""
    try:
        src = driver.page_source.lower()
        return any(kw in src for kw in _CAPTCHA_KEYWORDS)
    except Exception:
        return False


def ensure_page_ready(driver: webdriver.Chrome, first_visit: bool) -> None:
    """In headless mode, log warnings instead of blocking for human input."""
    if has_captcha(driver):
        logger.warning("CAPTCHA detected — page may be blocked")
    elif first_visit:
        logger.info("First visit — page loaded")
