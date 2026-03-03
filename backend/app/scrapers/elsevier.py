"""
app/scrapers/elsevier.py
────────────────────────
Elsevier / ScienceDirect — API-based scraper.
DOI prefix: 10.1016
"""

from app.scrapers.api_base import APIScraperBase


class ElsevierScraper(APIScraperBase):
    SOURCE_NAME = "ScienceDirect (Elsevier)"
    PUBLISHER = "Elsevier"
    OPENALEX_PUBLISHER = "Elsevier"
    DOI_PREFIX = "10.1016"
