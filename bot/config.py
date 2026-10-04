import re
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Effort = Literal["low", "medium", "high", "xhigh", "max"]
_TELEGRAM_TOKEN_RE = re.compile(r"\d{6,12}:[A-Za-z0-9_-]{30,40}")


class Settings(BaseSettings):
    """Application settings, read from environment / `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Without this, validation errors echo (parts of) the raw input, i.e. our secrets.
        hide_input_in_errors=True,
    )

    telegram_bot_token: SecretStr
    anthropic_api_key: SecretStr
    llm_model: str = Field(min_length=1)
    # Reasoning depth; extraction needs less than coding. None omits the parameter, which is
    # required for models that reject it (e.g. Haiku 4.5).
    llm_effort: Effort | None = "medium"

    default_lang: Literal["ru", "en", "kk"] = "ru"

    max_download_mb: int = Field(default=15, gt=0)
    max_chars: int = Field(default=120_000, gt=0)
    max_pdf_pages: int = Field(default=80, gt=0)
    request_timeout_s: float = Field(default=20, gt=0)
    rate_limit_per_hour: int = Field(default=10, gt=0)
    max_concurrent_jobs: int = Field(default=4, gt=0)  # requests processed at the same time
    # Linked pages read when the user's page has no deadline backed by its text (0 turns it off).
    follow_linked_pages: int = Field(default=2, ge=0, le=5)
    cache_ttl_hours: int = Field(default=24, ge=0)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("telegram_bot_token")
    @classmethod
    def _check_telegram_token(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        if not token.isascii():
            raise ValueError(
                "TELEGRAM_BOT_TOKEN contains non-ASCII characters "
                "(typed with a non-Latin keyboard layout while pasting?)"
            )
        if not _TELEGRAM_TOKEN_RE.fullmatch(token):
            raise ValueError("TELEGRAM_BOT_TOKEN must look like '<digits>:<35 characters>'")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
