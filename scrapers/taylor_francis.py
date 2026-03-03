"""scrapers/taylor_francis.py — Taylor & Francis (DOI prefix: 10.1080)"""
from scrapers.api_base import APIScraperBase

class TaylorFrancisScraper(APIScraperBase):
    SOURCE_NAME = "Taylor & Francis"
    PUBLISHER = "Taylor & Francis"
    OPENALEX_PUBLISHER = "Taylor & Francis"
    DOI_PREFIX = "10.1080"
