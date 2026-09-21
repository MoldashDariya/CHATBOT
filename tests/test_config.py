import pytest
from pydantic import ValidationError

from bot.config import Settings

REQUIRED = {
    "TELEGRAM_BOT_TOKEN": "123:abc",
    "ANTHROPIC_API_KEY": "sk-test",
    "LLM_MODEL": "some-model",
}


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*REQUIRED, "DEFAULT_LANG", "LOG_LEVEL", "MAX_DOWNLOAD_MB"):
        monkeypatch.delenv(name, raising=False)


def test_defaults_and_secrets_are_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)

    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.default_lang == "ru"
    assert settings.max_download_mb == 15
    assert settings.request_timeout_s == 20
    assert settings.telegram_bot_token.get_secret_value() == "123:abc"
    assert "123:abc" not in repr(settings)


def test_missing_required_settings_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_invalid_values_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("MAX_DOWNLOAD_MB", "0")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]
