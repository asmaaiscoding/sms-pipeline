from app.scrapers.acm import ACMScraper
from app.scrapers.ieee import IEEEScraper
from app.scrapers.springer import SpringerScraper
from app.scrapers.wiley import WileyScraper
from app.scrapers.elsevier import ElsevierScraper
from app.scrapers.taylor_francis import TaylorFrancisScraper
from app.scrapers.mdpi import MDPIScraper
from app.scrapers.sage import SageScraper
from app.scrapers.scopus import ScopusScraper
from app.scrapers.base import setup_driver

__all__ = [
    "ACMScraper",
    "IEEEScraper",
    "SpringerScraper",
    "WileyScraper",
    "ElsevierScraper",
    "TaylorFrancisScraper",
    "MDPIScraper",
    "SageScraper",
    "ScopusScraper",
    "setup_driver",
]
