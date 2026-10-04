"""What the user sees of the cache: an instant answer for a repeated link that does not use up
the hourly limit and says that it is saved, plus the refresh button and what pressing it does."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message

from bot.errors import LLMError
from bot.handlers import refresh as refresh_module
from bot.handlers.link import handle_text
from bot.handlers.refresh import PREFIX, handle_refresh, refresh_keyboard
from bot.render.texts import text
from bot.security.limits import RateLimiter
from tests.summaries import make_summary
from tests.test_handlers import URL, FakePipeline, make_message, make_settings

BUTTON = "0123456789abcdef"


def keyboard_of(call: Any) -> InlineKeyboardMarkup | None:
    markup = call.kwargs.get("reply_markup")
    assert markup is None or isinstance(markup, InlineKeyboardMarkup)
    return markup


def callback_data(markup: InlineKeyboardMarkup) -> str:
    data = markup.inline_keyboard[0][0].callback_data
    assert data is not None
    return data


async def send_link(
    pipeline: FakePipeline, *, limiter: RateLimiter | None = None, body: str = URL
) -> MagicMock:
    message = make_message(body)
    await handle_text(message, limiter or RateLimiter(limit=10), pipeline, make_settings())  # type: ignore[arg-type]
    return message


# --- a repeated link ----------------------------------------------------------------------


async def test_a_stored_answer_is_sent_at_once_without_running_the_pipeline() -> None:
    pipeline = FakePipeline()
    pipeline.stored = make_summary(language="ru", cached_minutes_ago=5)

    message = await send_link(pipeline)

    assert pipeline.calls == []
    message.answer.assert_awaited_once()  # the card itself, no "reading the page..." first
    body = message.answer.await_args.args[0]
    assert "🕒" in body and "5 мин назад" in body
    assert body.rstrip().splitlines()[-1].startswith("🔎")  # the source is still the last line


async def test_a_stored_answer_does_not_use_up_the_hourly_limit() -> None:
    limiter = RateLimiter(limit=1)
    stored = FakePipeline()
    stored.stored = make_summary(language="ru", cached_minutes_ago=1)

    for _ in range(5):
        await send_link(stored, limiter=limiter)
    fresh = FakePipeline()
    await send_link(fresh, limiter=limiter)  # the one real request the limit allows
    blocked = FakePipeline()
    message = await send_link(blocked, limiter=limiter)

    assert blocked.calls == [] and fresh.calls == [(URL, "ru")]
    assert "Слишком много запросов" in message.answer.await_args.args[0]


async def test_a_stored_answer_is_still_served_to_a_user_who_hit_the_limit() -> None:
    limiter = RateLimiter(limit=1)
    await send_link(FakePipeline(), limiter=limiter)  # uses the only allowed request
    pipeline = FakePipeline()
    pipeline.stored = make_summary(language="ru", cached_minutes_ago=3)

    message = await send_link(pipeline, limiter=limiter)

    assert "Слишком много" not in message.answer.await_args.args[0]
    assert "сохранённый ответ" in message.answer.await_args.args[0]


async def test_extra_links_are_still_mentioned_when_the_first_is_stored() -> None:
    pipeline = FakePipeline()
    pipeline.stored = make_summary(language="ru", cached_minutes_ago=1)

    message = await send_link(pipeline, body=f"{URL} https://other.edu/page")

    texts = [call.args[0] for call in message.answer.await_args_list]
    assert text("ru", "multiple_urls") in texts


async def test_a_fresh_request_keeps_the_reading_status_first() -> None:
    pipeline = FakePipeline()

    message = await send_link(pipeline)

    assert message.answer.await_args_list[0].args[0] == text("ru", "status_fetch")


# --- the button ---------------------------------------------------------------------------


async def test_the_card_carries_a_refresh_button_that_names_its_page_by_id() -> None:
    pipeline = FakePipeline()
    pipeline.button_id = BUTTON

    message = await send_link(pipeline)

    edit = message.answer.return_value.edit_text.await_args  # the status message became the card
    markup = keyboard_of(edit)
    assert markup is not None
    assert callback_data(markup) == f"{PREFIX}{BUTTON}"
    assert markup.inline_keyboard[0][0].text == "🔄 Обновить"
    assert URL not in callback_data(markup)  # no address in the callback


async def test_the_button_is_only_under_the_last_message_of_a_long_card() -> None:
    pipeline = FakePipeline()
    pipeline.button_id = BUTTON
    pipeline.result = make_summary(
        language="ru", key_points=[f"Пункт {n}: " + "слово " * 60 for n in range(40)]
    )

    message = await send_link(pipeline)

    sent = [message.answer.return_value.edit_text.await_args, *message.answer.await_args_list[1:]]
    assert len(sent) >= 2
    assert [keyboard_of(call) is not None for call in sent] == [False] * (len(sent) - 1) + [True]


async def test_no_button_when_nothing_is_stored_for_it() -> None:
    pipeline = FakePipeline()  # button_id None: the cache is off or could not save the answer

    message = await send_link(pipeline)

    assert keyboard_of(message.answer.return_value.edit_text.await_args) is None


async def test_a_failure_while_building_the_button_does_not_cost_the_card() -> None:
    pipeline = FakePipeline()
    pipeline.refresh_button_id = AsyncMock(side_effect=RuntimeError("db gone"))  # type: ignore[method-assign]

    message = await send_link(pipeline)

    edit = message.answer.return_value.edit_text.await_args
    assert "📌" in edit.args[0] and keyboard_of(edit) is None


async def test_the_button_survives_the_plain_text_fallback() -> None:
    pipeline = FakePipeline()
    pipeline.button_id = BUTTON
    message = make_message(URL)
    first = MagicMock()

    async def refuse_html(*args: Any, **kwargs: Any) -> None:
        if kwargs.get("parse_mode"):  # only the card in HTML is refused, status texts are fine
            raise TelegramBadRequest(method=MagicMock(), message="can't parse entities")

    first.edit_text = AsyncMock(side_effect=refuse_html)
    message.answer = AsyncMock(return_value=first)

    await handle_text(message, RateLimiter(limit=10), pipeline, make_settings())  # type: ignore[arg-type]

    last = first.edit_text.await_args_list[-1]
    assert last.kwargs.get("parse_mode") is None and keyboard_of(last) is not None


async def test_refresh_keyboard_is_none_without_a_stored_answer() -> None:
    assert await refresh_keyboard(FakePipeline(), URL, "ru") is None  # type: ignore[arg-type]


@pytest.mark.parametrize(("language", "label"), [("en", "🔄 Refresh"), ("kk", "🔄 Жаңарту")])
async def test_the_button_label_follows_the_language(language: str, label: str) -> None:
    pipeline = FakePipeline()
    pipeline.button_id = BUTTON

    markup = await refresh_keyboard(pipeline, URL, language)  # type: ignore[arg-type]

    assert markup is not None and markup.inline_keyboard[0][0].text == label


# --- pressing it --------------------------------------------------------------------------


class RefreshPipeline(FakePipeline):
    def __init__(self, target: tuple[str, str] | None = (URL, "ru"), **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.target = target
        self.refresh_flags: list[bool] = []
        self.button_id = BUTTON
        self.gate: asyncio.Event | None = None

    async def refresh_target(self, button_id: str) -> tuple[str, str] | None:
        return self.target if button_id == BUTTON else None

    async def run(
        self, url: str, *, language: str, on_stage: Any = None, refresh: bool = False
    ) -> Any:
        self.refresh_flags.append(refresh)
        if self.gate is not None:
            await self.gate.wait()
        return await super().run(url, language=language, on_stage=on_stage, refresh=refresh)


def make_callback(data: str = f"{PREFIX}{BUTTON}", *, accessible: bool = True) -> MagicMock:
    callback = MagicMock()
    callback.data = data
    callback.from_user.id = 7
    callback.answer = AsyncMock()
    message = MagicMock(spec=Message)
    message.answer = AsyncMock()
    status = MagicMock()
    status.edit_text = AsyncMock()
    message.answer.return_value = status
    message.edit_reply_markup = AsyncMock()
    callback.message = message if accessible else None
    return callback


async def press(
    callback: MagicMock, pipeline: RefreshPipeline, limiter: RateLimiter | None = None
) -> None:
    await handle_refresh(callback, limiter or RateLimiter(limit=10), pipeline, make_settings())  # type: ignore[arg-type]


async def test_pressing_the_button_reads_the_page_again_and_replaces_the_buttons() -> None:
    pipeline = RefreshPipeline()
    callback = make_callback()

    await press(callback, pipeline)

    assert pipeline.refresh_flags == [True] and pipeline.calls == [(URL, "ru")]
    callback.answer.assert_awaited_once_with(text("ru", "refresh_started"))
    callback.message.edit_reply_markup.assert_awaited_once_with(reply_markup=None)  # old button
    card = callback.message.answer.return_value.edit_text.await_args
    assert "📌" in card.args[0]
    new_markup = keyboard_of(card)
    assert new_markup is not None and callback_data(new_markup) == f"{PREFIX}{BUTTON}"


async def test_the_language_is_the_one_of_the_stored_answer() -> None:
    pipeline = RefreshPipeline(target=(URL, "en"), result=make_summary(language="en"))
    callback = make_callback()

    await press(callback, pipeline)

    assert pipeline.calls == [(URL, "en")]
    callback.answer.assert_awaited_once_with(text("en", "refresh_started"))
    assert callback.message.answer.await_args_list[0].args[0] == text("en", "status_fetch")


@pytest.mark.parametrize(
    "data", [f"{PREFIX}ffffffffffffffff", PREFIX, f"{PREFIX}' OR '1'='1", f"{PREFIX}{BUTTON}x"]
)
async def test_an_unknown_or_expired_button_says_so_and_runs_nothing(data: str) -> None:
    pipeline = RefreshPipeline()
    callback = make_callback(data)
    limiter = RateLimiter(limit=1)

    await press(callback, pipeline, limiter)

    callback.answer.assert_awaited_once_with(text("ru", "refresh_expired"), show_alert=True)
    assert pipeline.calls == []
    assert limiter.acquire(7).allowed  # the expired press did not use up the user's limit


async def test_a_message_that_is_no_longer_accessible_counts_as_expired() -> None:
    pipeline = RefreshPipeline()
    callback = make_callback(accessible=False)

    await press(callback, pipeline)

    callback.answer.assert_awaited_once_with(text("ru", "refresh_expired"), show_alert=True)
    assert pipeline.calls == []


async def test_a_refresh_counts_against_the_hourly_limit() -> None:
    limiter = RateLimiter(limit=1)
    pipeline = RefreshPipeline()

    await press(make_callback(), pipeline, limiter)
    blocked = make_callback()
    await press(blocked, pipeline, limiter)

    assert pipeline.calls == [(URL, "ru")]
    notice = blocked.answer.await_args
    assert "Слишком много запросов" in notice.args[0] and notice.kwargs == {"show_alert": True}
    blocked.message.edit_reply_markup.assert_not_awaited()  # the old answer keeps its button


async def test_a_double_tap_starts_one_job_not_two() -> None:
    pipeline = RefreshPipeline()
    pipeline.gate = asyncio.Event()
    first, second = make_callback(), make_callback()

    running = asyncio.create_task(press(first, pipeline))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await press(second, pipeline)  # while the first is still reading the page
    pipeline.gate.set()
    await running

    assert pipeline.refresh_flags == [True]
    second.answer.assert_awaited_once_with(text("ru", "refresh_busy"))


async def test_after_a_refresh_the_same_button_can_be_used_again() -> None:
    pipeline = RefreshPipeline()

    await press(make_callback(), pipeline)
    await press(make_callback(), pipeline)

    assert pipeline.refresh_flags == [True, True]
    assert refresh_module._in_progress == set()


async def test_a_failed_refresh_tells_the_user_and_keeps_the_old_button() -> None:
    pipeline = RefreshPipeline(error=LLMError("llm_busy"))
    callback = make_callback()

    await press(callback, pipeline)

    callback.message.edit_reply_markup.assert_not_awaited()
    shown = callback.message.answer.return_value.edit_text.await_args.args[0]
    assert "📌" not in shown and "Traceback" not in shown
    assert refresh_module._in_progress == set()  # and a retry is possible
    await press(make_callback(), RefreshPipeline())


async def test_old_buttons_that_cannot_be_removed_do_not_break_the_refresh() -> None:
    pipeline = RefreshPipeline()
    callback = make_callback()
    callback.message.edit_reply_markup = AsyncMock(
        side_effect=TelegramBadRequest(method=MagicMock(), message="message can't be edited")
    )

    await press(callback, pipeline)

    assert pipeline.refresh_flags == [True]
