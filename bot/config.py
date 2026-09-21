from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from environment / `.env`."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_bot_token: SecretStr
    anthropic_api_key: SecretStr
    llm_model: str = Field(min_length=1)

    default_lang: Literal["ru", "en", "kk"] = "ru"

    max_download_mb: int = Field(default=15, gt=0)
    max_chars: int = Field(default=120_000, gt=0)
    max_pdf_pages: int = Field(default=80, gt=0)
    request_timeout_s: float = Field(default=20, gt=0)
    rate_limit_per_hour: int = Field(default=10, gt=0)
    cache_ttl_hours: int = Field(default=24, ge=0)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
