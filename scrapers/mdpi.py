"""scrapers/mdpi.py — MDPI (DOI prefix: 10.3390)"""
from scrapers.api_base import APIScraperBase

class MDPIScraper(APIScraperBase):
    SOURCE_NAME = "MDPI"
    PUBLISHER = "MDPI"
    OPENALEX_PUBLISHER = "MDPI"
    DOI_PREFIX = "10.3390"
