"""scrapers/sage.py — SAGE Publications (DOI prefix: 10.1177)"""
from scrapers.api_base import APIScraperBase

class SageScraper(APIScraperBase):
    SOURCE_NAME = "SAGE Journals"
    PUBLISHER = "SAGE"
    OPENALEX_PUBLISHER = "SAGE"
    DOI_PREFIX = "10.1177"
