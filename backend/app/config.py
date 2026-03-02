"""
app/config.py
─────────────
Centralised settings loaded from environment variables / .env file.
Uses pydantic-settings for validation and type coercion.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ───────────────────────────────────────────────────────────────────
    APP_NAME: str = "SMS Pipeline"
    APP_VERSION: str = "1.0.0"
    SECRET_KEY: str = "change-me"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://sms:sms_secret@localhost:5432/sms_pipeline"
    DATABASE_URL_SYNC: str = "postgresql+psycopg2://sms:sms_secret@localhost:5432/sms_pipeline"

    # ── Redis ─────────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── Celery ────────────────────────────────────────────────────────────────
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # ── Auth ──────────────────────────────────────────────────────────────────
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 h
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    ALGORITHM: str = "HS256"

    # ── Scraping ──────────────────────────────────────────────────────────────
    SELENIUM_HEADLESS: bool = True
    SCRAPE_DELAY_MIN: float = 2.0
    SCRAPE_DELAY_MAX: float = 5.0
    SCRAPE_MAX_RETRIES: int = 3

    # ── CORS ──────────────────────────────────────────────────────────────────
    CORS_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:5173"]

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _parse_cors(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return [i.strip() for i in v.split(",")]
        return v

    # ── Deduplication ─────────────────────────────────────────────────────────
    TITLE_SIM_THRESHOLD: float = 0.92

    # ── PDF Finder ────────────────────────────────────────────────────────────
    PDF_SEARCH_TIMEOUT: int = 15
    PDF_SEARCH_SLEEP: float = 1.0

    # ── Preset search strings ────────────────────────────────────────────────
    @staticmethod
    def detection_strings() -> dict[str, str]:
        return {
            "DET-1": (
                '("vulnerability" OR "vulnerabilities" OR "security flaw" OR "security weakness") '
                'AND ("detection" OR "identification" OR "prediction" OR "discovery" OR "analysis") '
                'AND ("software" OR "application" OR "source code" OR "program")'
            ),
            "DET-2": (
                '("static analysis" OR "static code analysis" OR "program analysis" OR "SAST") '
                'AND ("vulnerability" OR "security flaw" OR "security weakness") '
                'AND ("software" OR "code" OR "application")'
            ),
            "DET-3": (
                '("vulnerability" OR "security vulnerability") '
                'AND ("machine learning" OR "deep learning" OR "neural network" OR "learning-based" OR "data-driven") '
                'AND ("software" OR "source code" OR "program")'
            ),
            "DET-4": (
                '("vulnerability detection" OR "vulnerability prediction") '
                'AND ("graph" OR "control flow graph" OR "program dependence graph" '
                'OR "code property graph" OR "graph neural network") '
                'AND ("software" OR "source code")'
            ),
            "DET-5": (
                '("vulnerability detection" OR "security vulnerability") '
                'AND ("large language model" OR "LLM" OR "foundation model") '
                'AND ("software" OR "code")'
            ),
        }

    @staticmethod
    def remediation_strings() -> dict[str, str]:
        return {
            "REM-1": (
                '("vulnerability" OR "vulnerabilities" OR "security flaw" OR "security weakness") '
                'AND ("remediation" OR "mitigation" OR "fix" OR "fixing" OR "repair" '
                'OR "patch" OR "patching") '
                'AND ("software" OR "code" OR "application" OR "source code")'
            ),
            "REM-2": (
                '("security refactoring" OR "secure refactoring" OR "refactoring") '
                'AND ("vulnerability" OR "security") '
                'AND ("software" OR "code" OR "application")'
            ),
            "REM-3": (
                '("automated" OR "automatic" OR "semi-automated") '
                'AND ("vulnerability remediation" OR "vulnerability mitigation" OR "security fix") '
                'AND ("software" OR "application")'
            ),
            "REM-4": (
                '("code transformation" OR "program transformation" OR "security hardening") '
                'AND ("vulnerability" OR "security flaw") '
                'AND ("remediation" OR "mitigation" OR "fix")'
            ),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
