"""
Centralized application configuration via pydantic-settings.

All environment variables are loaded from .env and validated at startup.
If a required variable is missing or invalid, the app fails fast with a clear error.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Resolve project root so we can reference paths relative to the repo
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # …/agent/


class Settings(BaseSettings):
    """
    Type-safe configuration for the InstaFlow platform.

    Every setting has a sensible default where safe to do so, and a clear
    docstring explaining its purpose.  Secrets (passwords, tokens) never
    have defaults — they MUST be set in .env or the environment.
    """

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ────────────────────────────────────────────────────
    app_name: str = "InstaFlow Automation Engine"
    app_env: str = Field(default="development", description="development | staging | production")
    debug: bool = Field(default=False, description="Enable debug logging and auto-reload")
    log_level: str = Field(default="INFO", description="Python log level")

    # ── FastAPI ────────────────────────────────────────────────────────
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # ── PostgreSQL ─────────────────────────────────────────────────────
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "instaflow"
    postgres_password: str = Field(..., description="DB password — MUST be set")
    postgres_db: str = "instaflow"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        """Sync URL for Alembic migrations."""
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    # ── Redis ──────────────────────────────────────────────────────────
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: Optional[str] = None

    @property
    def redis_url(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    # ── Celery ─────────────────────────────────────────────────────────
    celery_broker_url: Optional[str] = None  # Falls back to redis_url
    celery_result_backend: Optional[str] = None  # Falls back to redis_url
    celery_concurrency: int = Field(default=3, ge=1, le=10)

    # ── Session Vault ──────────────────────────────────────────────────
    vault_dir: str = str(BASE_DIR / "vaults")
    vault_encryption_key: str = Field(..., description="Fernet key for session encryption")

    # ── Proxy ──────────────────────────────────────────────────────────
    default_proxy_provider: str = "smartproxy"

    # ── Telegram Alerting ──────────────────────────────────────────────
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    # ── Instagram ──────────────────────────────────────────────────────
    ig_app_version: str = "302.1.0.36.111"
    ig_locale: str = "en_US"
    ig_timezone_offset: int = 19800  # IST = UTC+5:30 = 19800 seconds

    # ── 2Captcha (optional) ────────────────────────────────────────────
    captcha_api_key: Optional[str] = None

    # ── Ollama / LLM (optional) ────────────────────────────────────────
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3"
    llm_comment_probability: float = Field(default=0.2, ge=0.0, le=1.0)

    # ── Rate Limits ────────────────────────────────────────────────────
    rate_follows_per_hour: int = 10
    rate_follows_per_day: int = 50
    rate_comments_per_hour: int = 12
    rate_comments_per_day: int = 60
    rate_dm_reads_per_hour: int = 30

    # ── Paths ──────────────────────────────────────────────────────────
    exports_dir: str = str(BASE_DIR / "exports")
    logs_dir: str = str(BASE_DIR / "logs")
    comments_yaml: str = str(BASE_DIR / "instaflow" / "config" / "comments.yaml")
    cta_keywords_yaml: str = str(BASE_DIR / "instaflow" / "config" / "cta_keywords.yaml")

    # ── Validators ─────────────────────────────────────────────────────
    @field_validator("app_env")
    @classmethod
    def validate_env(cls, v: str) -> str:
        allowed = {"development", "staging", "production"}
        if v not in allowed:
            raise ValueError(f"app_env must be one of {allowed}, got '{v}'")
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        v = v.upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if v not in allowed:
            raise ValueError(f"log_level must be one of {allowed}, got '{v}'")
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Singleton accessor — import this everywhere, never instantiate Settings directly."""
    return Settings()
