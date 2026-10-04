import logging
import re
from pathlib import Path

import pytest

from bot.errors import (
    BlockedURLError,
    BotError,
    ContentTooLargeError,
    ExtractionError,
    FetchFailedError,
    FetchHTTPError,
    FetchTimeoutError,
    HostNotFoundError,
    InvalidURLError,
    LegacyOfficeFormatError,
    LLMError,
    TooLittleTextError,
    TooManyRedirectsError,
    UnsupportedFormatError,
)
from bot.render import errors as errors_module
from bot.render.errors import describe_error, handle_error
from bot.render.texts import TEXTS, text

LANGUAGES = list(TEXTS)
BOT_ROOT = Path(__file__).parent.parent / "bot"

CASES: list[tuple[BaseException, str, dict[str, object]]] = [
    (InvalidURLError("malformed URL"), "err_bad_url", {}),
    (BlockedURLError("host resolves to a non-public address"), "err_bad_url", {}),
    (HostNotFoundError("example.invalid"), "err_host", {}),
    (FetchTimeoutError("no response within 20s"), "err_timeout", {}),
    (FetchFailedError("could not connect"), "err_connect", {}),
    (TooManyRedirectsError("more than 5 redirects"), "err_redirects", {}),
    (ContentTooLargeError("declared 99999999 bytes"), "err_too_large", {"mb": 15}),
    (LegacyOfficeFormatError(), "err_legacy_office", {}),
    (UnsupportedFormatError("other"), "err_unsupported", {}),
    (UnsupportedFormatError("surprise-new-reason"), "err_unsupported", {}),
    (ExtractionError("surprise-new-reason"), "err_corrupt", {}),
    (LLMError("surprise-new-reason"), "err_llm_retry", {}),
    (RuntimeError("secret internal detail"), "err_generic", {}),
    (ValueError("unsupported language: 'fr'"), "err_generic", {}),
]


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize(("error", "key", "values"), CASES, ids=lambda v: type(v).__name__)
def test_each_error_has_its_own_message(
    error: BaseException, key: str, values: dict[str, object], language: str
) -> None:
    assert describe_error(error, language) == text(language, key, **values)


@pytest.mark.parametrize(
    ("status", "key"),
    [
        (401, "err_http_auth"),
        (402, "err_http_auth"),
        (403, "err_http_auth"),
        (407, "err_http_auth"),
        (451, "err_http_auth"),
        (404, "err_http_gone"),
        (410, "err_http_gone"),
        (429, "err_http_busy"),
        (500, "err_http_server"),
        (502, "err_http_server"),
        (503, "err_http_server"),
        (599, "err_http_server"),
        (400, "err_http_other"),
        (418, "err_http_other"),
        (302, "err_http_other"),  # a redirect without a Location header
    ],
)
def test_http_statuses(status: int, key: str) -> None:
    message = describe_error(FetchHTTPError(status), "ru")

    assert message == text("ru", key, code=status)


def test_a_blocked_site_message_suggests_what_to_do() -> None:
    assert "PDF" in describe_error(FetchHTTPError(403), "ru")
    assert "PDF" in describe_error(FetchHTTPError(403), "en")


def test_size_limit_is_taken_from_the_settings() -> None:
    assert "40 МБ" in describe_error(ContentTooLargeError("x"), "ru", max_download_mb=40)
    assert "40 MB" in describe_error(ContentTooLargeError("x"), "en", max_download_mb=40)


@pytest.mark.parametrize(
    ("source_format", "key"),
    [
        ("html", "err_thin_html"),
        ("pdf", "err_thin_file"),
        ("docx", "err_thin_file"),
        ("youtube", "err_thin_video"),
    ],
)
def test_too_little_text_depends_on_what_was_read(source_format: str, key: str) -> None:
    assert describe_error(TooLittleTextError(source_format, 120, 300), "ru") == text("ru", key)


def test_a_thin_web_page_names_the_likely_causes() -> None:
    message = describe_error(TooLittleTextError("html", 50, 300), "ru")

    for cause in ("вход", "платный", "JavaScript"):
        assert cause in message


# --- every reason that exists in the code has a message -----------------------------------


def reasons_raised_in_the_code(error_class: str) -> set[str]:
    pattern = re.compile(rf"{error_class}\(\s*\"([a-z_]+)\"")
    found: set[str] = set()
    for path in BOT_ROOT.rglob("*.py"):
        found.update(pattern.findall(path.read_text(encoding="utf-8")))
    return found


@pytest.mark.parametrize(
    ("error_class", "mapping", "fallback"),
    [
        ("ExtractionError", errors_module._EXTRACTION_KEYS, "err_corrupt"),
        ("LLMError", errors_module._LLM_KEYS, "err_llm_retry"),
        ("UnsupportedFormatError", errors_module._UNSUPPORTED_KEYS, "err_unsupported"),
    ],
)
def test_no_reason_raised_anywhere_is_left_without_a_message(
    error_class: str, mapping: dict[str, str], fallback: str
) -> None:
    raised = reasons_raised_in_the_code(error_class)

    assert raised, f"found no {error_class}(...) in the code: the scan is broken"
    # "other" is the deliberate catch-all of UnsupportedFormatError.
    missing = {reason for reason in raised if reason not in mapping and reason != "other"}
    assert not missing, f"{error_class} reasons without a user message: {sorted(missing)}"


def test_reasons_listed_in_the_docstrings_are_all_mapped() -> None:
    documented_extraction = set(re.findall(r'"([a-z_]+)"', ExtractionError.__doc__ or ""))
    documented_llm = set(re.findall(r'"(llm_[a-z_]+)"', LLMError.__doc__ or ""))

    assert documented_extraction <= set(errors_module._EXTRACTION_KEYS)
    assert documented_llm <= set(errors_module._LLM_KEYS)


def test_every_message_key_used_by_the_mappings_exists_in_every_language() -> None:
    keys = {
        *errors_module._UNSUPPORTED_KEYS.values(),
        *errors_module._EXTRACTION_KEYS.values(),
        *errors_module._THIN_KEYS.values(),
        *errors_module._LLM_KEYS.values(),
        "err_thin_video",
    }
    for language in LANGUAGES:
        assert keys <= set(TEXTS[language]), (language, keys - set(TEXTS[language]))


# --- nothing internal reaches the user ----------------------------------------------------

LEAKY = [
    BlockedURLError("host resolves to a non-public address 10.0.0.5"),
    HostNotFoundError("internal.corp.example"),
    FetchFailedError("RemoteProtocolError"),
    ExtractionError("unsafe_archive"),
    LLMError("llm_config"),
    RuntimeError("Traceback (most recent call last): sk-ant-secret"),
]


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("error", LEAKY, ids=lambda e: type(e).__name__)
def test_no_internal_detail_is_shown(error: BaseException, language: str) -> None:
    message = describe_error(error, language)

    assert str(error) not in message
    for forbidden in ("Traceback", "sk-ant", "10.0.0.5", "internal.corp", "SSRF", "non-public"):
        assert forbidden not in message
    assert type(error).__name__ not in message


def test_a_bad_api_key_or_model_is_not_blamed_on_the_user() -> None:
    message = describe_error(LLMError("llm_config"), "ru")

    assert "ключ" not in message.lower() and "модел" not in message.lower()
    assert message == text("ru", "err_service")


@pytest.mark.parametrize("language", LANGUAGES)
def test_describe_error_never_returns_an_empty_message(language: str) -> None:
    for error, _, _ in CASES:
        assert describe_error(error, language).strip()


def test_unknown_language_falls_back_to_russian() -> None:
    assert describe_error(HostNotFoundError("x"), "xx") == text("ru", "err_host")


# --- logging ------------------------------------------------------------------------------


def levels(caplog: pytest.LogCaptureFixture) -> list[int]:
    return [record.levelno for record in caplog.records if record.name == errors_module.__name__]


def test_describe_error_does_not_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        describe_error(RuntimeError("x"), "ru")
    assert caplog.records == []


@pytest.mark.parametrize(
    "error",
    [
        InvalidURLError("x"),
        HostNotFoundError("x"),
        FetchHTTPError(404),
        FetchTimeoutError("x"),
        ExtractionError("scanned_pdf"),
        TooLittleTextError("html", 10, 300),
        LLMError("llm_busy"),
    ],
    ids=lambda e: type(e).__name__,
)
def test_expected_failures_are_logged_quietly(
    error: BotError, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        handle_error(error, "ru")

    assert levels(caplog) == [logging.INFO]


@pytest.mark.parametrize("reason", ["llm_config", "llm_failed"])
def test_problems_only_the_operator_can_fix_are_logged_as_errors(
    reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        handle_error(LLMError(reason), "ru")

    assert levels(caplog) == [logging.ERROR]


def test_a_bug_is_logged_with_its_traceback_and_the_user_gets_a_neutral_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    try:
        raise RuntimeError("secret internal detail")
    except RuntimeError as exc:
        with caplog.at_level(logging.DEBUG):
            message = handle_error(exc, "en")

    (record,) = [r for r in caplog.records if r.name == errors_module.__name__]
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
    assert message == text("en", "err_generic")
    assert "secret internal detail" not in message


def test_logs_never_contain_the_message_of_a_user_facing_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        handle_error(InvalidURLError("https://private.example/?token=abc123"), "ru")

    assert "abc123" not in caplog.text
