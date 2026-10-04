import pytest
from pydantic import ValidationError

from bot.config import Settings

FAKE_TOKEN = "1234567890:" + "A" * 35

REQUIRED = {
    "TELEGRAM_BOT_TOKEN": FAKE_TOKEN,
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
    assert settings.telegram_bot_token.get_secret_value() == FAKE_TOKEN
    assert FAKE_TOKEN not in repr(settings)


def test_missing_required_settings_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("token", "expected_hint"),
    [
        (FAKE_TOKEN + "св", "non-ASCII"),  # Cyrillic "с"/"в" = Ctrl+C/V on a Russian layout
        ("not-a-token", "must look like"),
        ("1234567890:short", "must look like"),
    ],
)
def test_malformed_telegram_token_rejected_without_leaking_it(
    monkeypatch: pytest.MonkeyPatch, token: str, expected_hint: str
) -> None:
    _clear_env(monkeypatch)
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)  # type: ignore[call-arg]

    message = str(exc_info.value)
    assert expected_hint in message
    # pydantic abbreviates long inputs, so check the raw-input echo itself, not only the full token.
    assert "input_value" not in message
    assert token[:12] not in message


def test_invalid_values_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("MAX_DOWNLOAD_MB", "0")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]
