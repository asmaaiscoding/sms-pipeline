"""
app/scrapers/mdpi.py
────────────────────
MDPI (Multidisciplinary Digital Publishing Institute) — API-based scraper.
DOI prefix: 10.3390
"""

from app.scrapers.api_base import APIScraperBase


class MDPIScraper(APIScraperBase):
    SOURCE_NAME = "MDPI"
    PUBLISHER = "MDPI"
    OPENALEX_PUBLISHER = "MDPI"
    DOI_PREFIX = "10.3390"
