"""Files sent into the chat: the handler's side (download, limits, replies)."""

import asyncio
from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError

from bot.errors import LegacyOfficeFormatError
from bot.handlers import document as document_module
from bot.handlers.document import display_name, handle_document, handle_other_media
from bot.pipeline.orchestrator import Stage
from bot.render.telegram import render_summary
from bot.render.texts import text
from bot.security.limits import RateLimiter
from tests.summaries import make_summary
from tests.test_handlers import edits, make_message, make_settings, status_of

PDF_BYTES = b"%PDF-1.4 fake bytes for the handler test"


class FakePipeline:
    """Stands in for the Orchestrator's `run_file`: records the call, reports stages, answers."""

    def __init__(
        self,
        error: BaseException | None = None,
        stages: Sequence[Stage] = (Stage.READING, Stage.ANALYZING),
    ) -> None:
        self.error = error
        self.stages = stages
        self.calls: list[dict[str, Any]] = []
        self.result = make_summary(language="ru", source_url="rules.pdf")

    async def run_file(self, body: bytes, *, on_stage: Any = None, **kwargs: Any) -> Any:
        self.calls.append({"body": body, **kwargs})
        for stage in self.stages:
            if on_stage:
                await on_stage(stage)
        if self.error:
            raise self.error
        return self.result


def make_document(**overrides: Any) -> MagicMock:
    values: dict[str, Any] = {
        "file_name": "rules.pdf",
        "mime_type": "application/pdf",
        "file_size": 2048,
    }
    values.update(overrides)
    document = MagicMock()
    for key, value in values.items():
        setattr(document, key, value)
    return document


def make_bot(content: bytes = PDF_BYTES, error: BaseException | None = None) -> MagicMock:
    async def download(file: Any, destination: Any, timeout: int = 30) -> None:  # noqa: ASYNC109
        if error:
            raise error
        destination.write(content)

    bot = MagicMock()
    bot.download = AsyncMock(side_effect=download)
    return bot


async def send(
    *,
    document: MagicMock | None = None,
    bot: MagicMock | None = None,
    pipeline: FakePipeline | None = None,
    limiter: RateLimiter | None = None,
    **settings: Any,
) -> tuple[MagicMock, MagicMock, FakePipeline]:
    message = make_message("")
    message.document = document if document is not None else make_document()
    bot = bot or make_bot()
    pipeline = pipeline or FakePipeline()
    await handle_document(
        message,
        bot,
        limiter or RateLimiter(limit=10),
        pipeline,
        make_settings(**settings),  # type: ignore[arg-type]
    )
    return message, bot, pipeline


# --- the happy path -----------------------------------------------------------------------


async def test_a_file_is_downloaded_summarised_and_shown_in_place_of_the_status_message() -> None:
    message, bot, pipeline = await send()

    assert message.answer.await_args_list[0].args[0] == text("ru", "status_download")
    (call,) = pipeline.calls
    assert call["body"] == PDF_BYTES
    assert call["filename"] == "rules.pdf"
    assert call["content_type"] == "application/pdf"
    assert call["language"] == "ru"
    assert call["display_name"] == "rules.pdf"
    card = render_summary(pipeline.result)[0]
    assert edits(message) == [text("ru", "status_read"), text("ru", "status_analyze"), card]
    assert status_of(message).edit_text.await_args_list[-1].kwargs["parse_mode"] == ParseMode.HTML
    bot.download.assert_awaited_once()


async def test_the_card_names_the_file_as_its_source() -> None:
    message, _, _ = await send()

    card = edits(message)[-1]
    assert card.splitlines()[-1] == "🔎 <b>Источник:</b> <code>rules.pdf</code>"


async def test_a_file_without_a_name_or_type_still_works() -> None:
    _, _, pipeline = await send(document=make_document(file_name=None, mime_type=None))

    (call,) = pipeline.calls
    assert (
        call["filename"] == "document"
        and call["content_type"] == ""
        and call["display_name"] == "file"
    )


async def test_progress_texts_follow_the_configured_language() -> None:
    message, _, _ = await send(default_lang="en")

    assert message.answer.await_args_list[0].args[0] == "⏳ Downloading the file…"


# --- limits and refusals ------------------------------------------------------------------


async def test_a_file_declared_too_big_is_refused_without_downloading_it() -> None:
    message, bot, pipeline = await send(document=make_document(file_size=16 * 1024 * 1024))

    assert edits(message)[-1] == text("ru", "err_too_large", mb=15)
    bot.download.assert_not_awaited()
    assert pipeline.calls == []


async def test_the_limit_comes_from_the_settings() -> None:
    message, bot, _ = await send(
        document=make_document(file_size=2 * 1024 * 1024), max_download_mb=1
    )

    assert "1 МБ" in edits(message)[-1]
    bot.download.assert_not_awaited()


async def test_an_unknown_size_is_not_a_reason_to_refuse() -> None:
    _, bot, pipeline = await send(document=make_document(file_size=None))

    bot.download.assert_awaited_once()
    assert len(pipeline.calls) == 1  # the pipeline checks the real size after the download


async def test_telegram_saying_the_file_is_too_big_is_reported_as_too_big() -> None:
    error = TelegramBadRequest(method=MagicMock(), message="Bad Request: file is too big")

    message, _, pipeline = await send(bot=make_bot(error=error))

    assert edits(message)[-1] == text("ru", "err_too_large", mb=15)
    assert pipeline.calls == []


@pytest.mark.parametrize(
    "error",
    [
        TelegramBadRequest(method=MagicMock(), message="Bad Request: wrong file_id"),
        TelegramNetworkError(method=MagicMock(), message="connection reset"),
    ],
    ids=["bad-request", "network"],
)
async def test_other_download_failures_get_a_neutral_message(error: Exception) -> None:
    message, _, pipeline = await send(bot=make_bot(error=error))

    assert edits(message)[-1] == text("ru", "err_download")
    assert "connection reset" not in "".join(edits(message))
    assert pipeline.calls == []


async def test_a_stalled_download_is_cut_off(monkeypatch: pytest.MonkeyPatch) -> None:
    async def hang(file: Any, destination: Any, timeout: int = 30) -> None:  # noqa: ASYNC109
        await asyncio.sleep(5)

    monkeypatch.setattr(document_module, "DOWNLOAD_TIMEOUT_S", 0.1)
    bot = MagicMock()
    bot.download = AsyncMock(side_effect=hang)

    message, _, pipeline = await send(bot=bot)

    assert edits(message)[-1] == text("ru", "err_processing_timeout")
    assert pipeline.calls == []


async def test_pipeline_errors_reach_the_user_as_friendly_text() -> None:
    message, _, _ = await send(pipeline=FakePipeline(error=LegacyOfficeFormatError()))

    assert edits(message)[-1] == text("ru", "err_legacy_office")


async def test_an_unexpected_failure_is_neutral() -> None:
    message, _, _ = await send(pipeline=FakePipeline(error=RuntimeError("secret internal detail")))

    assert edits(message)[-1] == text("ru", "err_generic")
    assert "secret" not in "".join(edits(message))


# --- the rate limit -----------------------------------------------------------------------


async def test_files_count_against_the_same_hourly_limit() -> None:
    limiter = RateLimiter(limit=1)
    await send(limiter=limiter)

    message, bot, pipeline = await send(limiter=limiter, default_lang="en")

    assert "Too many requests" in message.answer.await_args.args[0]
    bot.download.assert_not_awaited()
    assert pipeline.calls == []


# --- other kinds of messages --------------------------------------------------------------


@pytest.mark.parametrize(
    ("language", "word"), [("ru", "PDF и DOCX"), ("en", "PDF and DOCX"), ("kk", "PDF және DOCX")]
)
async def test_photos_voice_and_the_like_get_a_hint_about_what_the_bot_reads(
    language: str, word: str
) -> None:
    message = make_message("")

    await handle_other_media(message, make_settings(default_lang=language))

    message.answer.assert_awaited_once()
    assert word in message.answer.await_args.args[0]


# --- the name shown as the source ---------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("rules.pdf", "rules.pdf"),
        ("  spaced   out  .pdf ", "spaced out .pdf"),
        ("line\nbreak\r\n.pdf", "line break .pdf"),
        ("bell\x07 and\x1b[31m escape.pdf", "bell and[31m escape.pdf"),
        ("zero\u200bwidth\u202eRTL.pdf", "zerowidthRTL.pdf"),
        ("Правила приёма.pdf", "Правила приёма.pdf"),
        ("", "file"),
        (None, "file"),
        ("\n\t ", "file"),
        ("x" * 500, "x" * 120),
    ],
)
def test_display_name(name: str | None, shown: str) -> None:
    assert display_name(name) == shown


async def test_a_hostile_file_name_is_shown_escaped_and_on_one_line() -> None:
    hostile = '<b>x</b>\n<a href="javascript:alert(1)">click</a>.pdf'
    pipeline = FakePipeline()
    pipeline.result = make_summary(language="ru", source_url=display_name(hostile))

    message, _, _ = await send(document=make_document(file_name=hostile), pipeline=pipeline)

    card = edits(message)[-1]
    source_line = card.splitlines()[-1]
    assert "<a href" not in source_line and "<b>x" not in source_line
    assert "&lt;b&gt;x&lt;/b&gt;" in source_line
    assert "\n" not in display_name(hostile)
