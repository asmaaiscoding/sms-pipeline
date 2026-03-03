"""
app/scrapers/sage.py
────────────────────
SAGE Publications — API-based scraper.
DOI prefix: 10.1177
"""

from app.scrapers.api_base import APIScraperBase


class SageScraper(APIScraperBase):
    SOURCE_NAME = "SAGE Journals"
    PUBLISHER = "SAGE"
    OPENALEX_PUBLISHER = "SAGE"
    DOI_PREFIX = "10.1177"
