import logging
from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError

from bot import messages
from bot.config import Settings
from bot.errors import FetchHTTPError, InvalidURLError, LLMError, TooLittleTextError
from bot.handlers.link import handle_text
from bot.handlers.start import cmd_help, cmd_start
from bot.handlers.status import StatusMessage, to_plain_text
from bot.main import build_dispatcher
from bot.pipeline.orchestrator import Stage
from bot.render.telegram import render_summary
from bot.render.texts import text
from bot.security.limits import RateLimiter
from tests.summaries import make_summary

URL = "https://example.edu/admissions"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "telegram_bot_token": "1234567890:" + "A" * 35,
        "anthropic_api_key": "x",
        "llm_model": "m",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


class FakePipeline:
    """Replaces the Orchestrator: reports some stages, then returns a result or raises."""

    def __init__(
        self,
        result: Any = None,
        error: BaseException | None = None,
        stages: Sequence[Stage] = (Stage.READING, Stage.ANALYZING),
    ) -> None:
        self.result = result if result is not None else make_summary(language="ru")
        self.error = error
        self.stages = stages
        self.calls: list[tuple[str, str]] = []

    async def run(self, url: str, *, language: str, on_stage: Any = None) -> Any:
        self.calls.append((url, language))
        for stage in self.stages:
            if on_stage:
                await on_stage(stage)
        if self.error:
            raise self.error
        return self.result


def make_message(message_text: str) -> MagicMock:
    message = MagicMock()
    message.text = message_text
    message.from_user.id = 42
    message.chat.id = 42
    status = MagicMock()
    status.edit_text = AsyncMock()
    message.answer = AsyncMock(return_value=status)
    return message


async def run_handler(
    message_text: str,
    pipeline: FakePipeline | None = None,
    *,
    limiter: RateLimiter | None = None,
    **settings: Any,
) -> tuple[MagicMock, FakePipeline]:
    message = make_message(message_text)
    pipeline = pipeline or FakePipeline()
    await handle_text(
        message,
        limiter or RateLimiter(limit=10),
        pipeline,
        make_settings(**settings),  # type: ignore[arg-type]
    )
    return message, pipeline


def status_of(message: MagicMock) -> MagicMock:
    return message.answer.return_value


def edits(message: MagicMock) -> list[str]:
    return [call.args[0] for call in status_of(message).edit_text.await_args_list]


# --- /start, /help ------------------------------------------------------------------------


async def test_start_and_help_reply() -> None:
    message = make_message("/start")
    await cmd_start(message)
    message.answer.assert_awaited_once_with(messages.START)

    message = make_message("/help")
    await cmd_help(message)
    assert message.answer.await_args.args[0] == messages.HELP


# --- before the pipeline ------------------------------------------------------------------


@pytest.mark.parametrize("language", ["ru", "en", "kk"])
async def test_text_without_a_link_asks_for_one_in_the_configured_language(language: str) -> None:
    message, pipeline = await run_handler("привет", default_lang=language)

    message.answer.assert_awaited_once_with(text(language, "no_url"))
    assert pipeline.calls == []


async def test_rate_limited_user_is_told_how_long_to_wait_and_nothing_runs() -> None:
    limiter = RateLimiter(limit=1)
    await run_handler(URL, limiter=limiter)

    message, pipeline = await run_handler(URL, limiter=limiter, default_lang="en")

    message.answer.assert_awaited_once()
    assert "Too many requests" in message.answer.await_args.args[0]
    assert pipeline.calls == []


async def test_chatter_does_not_use_up_the_limit() -> None:
    limiter = RateLimiter(limit=1)
    await run_handler("привет", limiter=limiter)

    _, pipeline = await run_handler(URL, limiter=limiter)

    assert pipeline.calls == [(URL, "ru")]


async def test_several_links_warn_and_the_first_is_processed() -> None:
    message, pipeline = await run_handler("https://a.edu/x https://b.edu/y")

    assert pipeline.calls == [("https://a.edu/x", "ru")]
    assert message.answer.await_args_list[0].args[0] == text("ru", "multiple_urls")


# --- the successful flow ------------------------------------------------------------------


async def test_status_message_follows_the_stages_and_becomes_the_card() -> None:
    message, pipeline = await run_handler(URL)

    assert message.answer.await_args_list[0].args[0] == text("ru", "status_fetch")
    card = render_summary(pipeline.result)[0]
    assert edits(message) == [text("ru", "status_read"), text("ru", "status_analyze"), card]
    assert status_of(message).edit_text.await_args_list[-1].kwargs["parse_mode"] == ParseMode.HTML
    assert message.answer.await_count == 1  # no extra message for a one-message card


async def test_the_pipeline_gets_the_first_link_and_the_configured_language() -> None:
    _, pipeline = await run_handler(f"смотри {URL}, пожалуйста", default_lang="kk")

    assert pipeline.calls == [(URL, "kk")]


async def test_a_long_card_continues_in_further_messages() -> None:
    items = [f"Пункт {n}: " + "очень длинное описание условия " * 12 for n in range(60)]
    big = make_summary(key_points=items, eligibility=items[:30], requirements=items[:30])
    cards = render_summary(big)
    assert len(cards) > 1

    message, _ = await run_handler(URL, FakePipeline(result=big))

    assert edits(message)[-1] == cards[0]  # the status message became the first part
    follow_ups = [c.args[0] for c in message.answer.await_args_list[1:]]
    assert follow_ups == cards[1:]
    assert all(c.kwargs["parse_mode"] == ParseMode.HTML for c in message.answer.await_args_list[1:])
    assert "🔎" in follow_ups[-1]  # the source ends the last one


async def test_progress_texts_are_localized() -> None:
    message, _ = await run_handler(
        URL, FakePipeline(make_summary(language="en")), default_lang="en"
    )

    assert message.answer.await_args_list[0].args[0] == "⏳ Opening the page…"
    assert edits(message)[:2] == ["📄 Reading the text…", "🧠 Analysing…"]


# --- failures reach the user as plain language --------------------------------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (InvalidURLError("malformed URL"), text("ru", "err_bad_url")),
        (FetchHTTPError(403), text("ru", "err_http_auth")),
        (FetchHTTPError(404), text("ru", "err_http_gone", code=404)),
        (TooLittleTextError("html", 40, 300), text("ru", "err_thin_html")),
        (LLMError("llm_busy"), text("ru", "err_llm_busy")),
        (LLMError("llm_config"), text("ru", "err_service")),
        (RuntimeError("secret internal detail"), text("ru", "err_generic")),
    ],
    ids=lambda v: type(v).__name__ if isinstance(v, BaseException) else "",
)
async def test_errors_replace_the_status_message_with_a_friendly_text(
    error: BaseException, expected: str
) -> None:
    message, _ = await run_handler(URL, FakePipeline(error=error))

    assert edits(message)[-1] == expected
    assert "secret internal detail" not in "".join(edits(message))
    assert "Traceback" not in "".join(edits(message))


async def test_the_size_limit_in_the_message_comes_from_the_settings() -> None:
    from bot.errors import ContentTooLargeError

    message, _ = await run_handler(
        URL, FakePipeline(error=ContentTooLargeError("x")), max_download_mb=40
    )

    assert "40 МБ" in edits(message)[-1]


async def test_a_bug_in_the_pipeline_is_logged_with_a_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR):
        await run_handler(URL, FakePipeline(error=RuntimeError("boom")))

    assert any(r.exc_info for r in caplog.records)


async def test_the_full_link_is_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        await run_handler("https://private.example/doc?token=abc123secret")

    assert "abc123secret" not in caplog.text
    assert "private.example" in caplog.text  # the host is enough to debug with


# --- Telegram refusing the card -----------------------------------------------------------


def bad_request(message: str) -> TelegramBadRequest:
    return TelegramBadRequest(method=MagicMock(), message=message)


async def test_when_telegram_rejects_the_markup_the_card_goes_out_as_plain_text() -> None:
    message = make_message(URL)
    status = status_of(message)
    status.edit_text.side_effect = [
        None,  # reading
        None,  # analysing
        bad_request("can't parse entities: Unsupported start tag"),  # the HTML card
        None,  # the plain-text retry
    ]

    await handle_text(message, RateLimiter(limit=10), FakePipeline(), make_settings())  # type: ignore[arg-type]

    html_try, plain_try = status.edit_text.await_args_list[2:4]
    assert html_try.kwargs["parse_mode"] == ParseMode.HTML
    assert plain_try.kwargs["parse_mode"] is None
    body = plain_try.args[0]
    assert "<b>" not in body and "<a " not in body
    assert "Undergraduate Admissions 2027" in body
    assert "https://www.example.edu/apply/online" in body  # link targets survive in plain text


async def test_if_the_status_message_cannot_be_edited_the_card_is_sent_as_a_new_message() -> None:
    message = make_message(URL)
    status_of(message).edit_text.side_effect = [
        None,
        None,
        bad_request("message to edit not found"),
        bad_request("message to edit not found"),
    ]

    await handle_text(message, RateLimiter(limit=10), FakePipeline(), make_settings())  # type: ignore[arg-type]

    last = message.answer.await_args_list[-1]
    assert "Undergraduate Admissions 2027" in last.args[0]
    assert last.kwargs["parse_mode"] == ParseMode.HTML


async def test_if_telegram_refuses_everything_the_handler_still_does_not_crash() -> None:
    message = make_message(URL)
    status_of(message).edit_text.side_effect = bad_request("nope")
    message.answer.side_effect = [status_of(message)] + [bad_request("nope")] * 10

    await handle_text(message, RateLimiter(limit=10), FakePipeline(), make_settings())  # type: ignore[arg-type]  # must not raise


async def test_a_network_error_while_reporting_the_failure_is_contained() -> None:
    message = make_message(URL)
    status_of(message).edit_text.side_effect = TelegramNetworkError(
        method=MagicMock(), message="down"
    )

    await handle_text(
        message,
        RateLimiter(limit=10),
        FakePipeline(error=InvalidURLError("x")),  # type: ignore[arg-type]
        make_settings(),
    )  # must not raise


# --- plain-text fallback ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("markup", "expected"),
    [
        ("<b>bold</b> and <i>italic</i>", "bold and italic"),
        ('<a href="https://x.org/a?b=1&amp;c=2">Portal</a>', "Portal (https://x.org/a?b=1&c=2)"),
        ('<a href="https://x.org/">https://x.org/</a>', "https://x.org/"),
        ("a &lt; b &amp; c &quot;d&quot;", 'a < b & c "d"'),
        ("<code>file:///etc/passwd</code>", "file:///etc/passwd"),
        ("plain text", "plain text"),
        ("", ""),
    ],
)
def test_plain_text_fallback(markup: str, expected: str) -> None:
    assert to_plain_text(markup) == expected


def test_plain_text_fallback_of_a_real_card_has_no_markup_left() -> None:
    cards = render_summary(make_summary())

    for card in cards:
        plain = to_plain_text(card)
        for tag in ("<b>", "</b>", "<i>", "</i>", "<a ", "</a>", "<code>"):
            assert tag not in plain
    last = to_plain_text(cards[-1])
    assert last.splitlines()[-1] == "🔎 Источник: https://www.example.edu/admissions/undergraduate"


# --- StatusMessage ------------------------------------------------------------------------


async def test_status_message_skips_identical_updates() -> None:
    message = make_message("x")
    status = StatusMessage(message)
    await status.update("a")
    await status.update("a")
    await status.update("b")

    message.answer.assert_awaited_once_with("a")
    status_of(message).edit_text.assert_awaited_once_with("b")


async def test_status_message_survives_a_failed_edit() -> None:
    message = make_message("x")
    status_of(message).edit_text = AsyncMock(side_effect=bad_request("message is not modified"))
    status = StatusMessage(message)
    await status.update("a")
    await status.update("b")  # must not raise


async def test_finish_without_a_prior_status_message_just_sends() -> None:
    message = make_message("x")
    await StatusMessage(message).finish(["<b>card</b>"])

    message.answer.assert_awaited_once_with("<b>card</b>", parse_mode=ParseMode.HTML)


# --- wiring -------------------------------------------------------------------------------


def test_dispatcher_routes_commands_before_the_catch_all_and_carries_the_shared_objects() -> None:
    limiter, pipeline, settings = RateLimiter(limit=10), FakePipeline(), make_settings()

    dp = build_dispatcher(limiter, pipeline, settings)  # type: ignore[arg-type]

    assert [router.name for router in dp.sub_routers] == ["start", "link"]
    assert dp.workflow_data["rate_limiter"] is limiter
    assert dp.workflow_data["pipeline"] is pipeline
    assert dp.workflow_data["settings"] is settings
