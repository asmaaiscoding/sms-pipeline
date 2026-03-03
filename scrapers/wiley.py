"""scrapers/wiley.py — Wiley Online Library (DOI prefix: 10.1002)"""
from scrapers.api_base import APIScraperBase

class WileyScraper(APIScraperBase):
    SOURCE_NAME = "Wiley Online Library"
    PUBLISHER = "Wiley"
    OPENALEX_PUBLISHER = "Wiley"
    DOI_PREFIX = "10.1002"
