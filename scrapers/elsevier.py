"""scrapers/elsevier.py — ScienceDirect / Elsevier (DOI prefix: 10.1016)"""
from scrapers.api_base import APIScraperBase

class ElsevierScraper(APIScraperBase):
    SOURCE_NAME = "ScienceDirect (Elsevier)"
    PUBLISHER = "Elsevier"
    OPENALEX_PUBLISHER = "Elsevier"
    DOI_PREFIX = "10.1016"
