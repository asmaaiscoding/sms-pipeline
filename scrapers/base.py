"""
scrapers/base.py
----------------
Shared Selenium utilities used by every source-specific scraper.

Provides:
  - Chrome driver setup with anti-bot measures
  - CAPTCHA detection + human-pause helper
  - Random delay helper
  - Retry-with-backoff wrapper
"""

import time
import random
from typing import Callable, Any

from selenium import webdriver
from selenium.webdriver.chrome.options import Options


# ═══════════════════════════════════════════════════════════════════════════════
#  Driver setup
# ═══════════════════════════════════════════════════════════════════════════════

def setup_driver() -> webdriver.Chrome:
    """Return a Chrome WebDriver with anti-detection tweaks."""
    opts = Options()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--window-size=1920,1080")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(options=opts)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
    )
    return driver


# ═══════════════════════════════════════════════════════════════════════════════
#  Delay & retry
# ═══════════════════════════════════════════════════════════════════════════════

def random_delay(lo: float = 2.0, hi: float = 5.0) -> None:
    time.sleep(random.uniform(lo, hi))


def retry(func: Callable, retries: int = 3, *args: Any, **kwargs: Any) -> Any:
    """Call *func(*args, **kwargs)* with exponential back-off on failure."""
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            if attempt < retries - 1:
                wait = (2 ** attempt) + random.uniform(0, 1)
                print(f"  [RETRY {attempt + 1}/{retries}] {exc}")
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
    """Heuristic: return *True* if the page looks like a CAPTCHA / block page."""
    try:
        src = driver.page_source.lower()
        return any(kw in src for kw in _CAPTCHA_KEYWORDS)
    except Exception:
        return False


def pause_for_human(reason: str = "") -> None:
    """Block until the user presses ENTER (e.g. after solving a CAPTCHA)."""
    print("\n" + "=" * 70)
    print("  HUMAN VERIFICATION REQUIRED")
    print("=" * 70)
    if reason:
        print(f"  {reason}")
    print("  Check the browser window.")
    print("  If there is a CAPTCHA or login prompt, handle it now.")
    input("  Press ENTER when the page is ready to continue...")
    print("=" * 70 + "\n")


def ensure_page_ready(driver: webdriver.Chrome, first_visit: bool) -> None:
    """If this is the first visit OR a CAPTCHA is detected, pause."""
    if first_visit or has_captcha(driver):
        reason = "First visit — please verify the page loaded correctly." if first_visit else "A CAPTCHA or block page was detected."
        pause_for_human(reason)
