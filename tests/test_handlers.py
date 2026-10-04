from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramBadRequest

from bot import messages
from bot.handlers.link import handle_text
from bot.handlers.start import cmd_help, cmd_start
from bot.handlers.status import StatusMessage
from bot.main import build_dispatcher
from bot.security.limits import RateLimiter


def make_message(text: str) -> MagicMock:
    message = MagicMock()
    message.text = text
    message.from_user.id = 42
    sent = MagicMock()
    sent.edit_text = AsyncMock()
    message.answer = AsyncMock(return_value=sent)
    return message


async def test_start_and_help_reply() -> None:
    message = make_message("/start")
    await cmd_start(message)
    message.answer.assert_awaited_once_with(messages.START)

    message = make_message("/help")
    await cmd_help(message)
    assert message.answer.await_args.args[0] == messages.HELP


async def test_text_without_url_asks_for_link() -> None:
    message = make_message("привет")
    await handle_text(message, RateLimiter(limit=10))
    message.answer.assert_awaited_once_with(messages.NO_URL)


async def test_link_sends_status_then_edits_it() -> None:
    message = make_message("https://example.edu/admissions")
    await handle_text(message, RateLimiter(limit=10))

    message.answer.assert_awaited_once_with(messages.STATUS_READING)
    sent = message.answer.return_value
    sent.edit_text.assert_awaited_once_with(messages.NOT_READY)


async def test_multiple_links_warns_and_processes_first() -> None:
    message = make_message("https://a.edu/x https://b.edu/y")
    await handle_text(message, RateLimiter(limit=10))

    texts = [call.args[0] for call in message.answer.await_args_list]
    assert texts == [messages.MULTIPLE_URLS, messages.STATUS_READING]


async def test_rate_limited_user_gets_wait_hint_and_no_status() -> None:
    limiter = RateLimiter(limit=1)
    await handle_text(make_message("https://a.edu/x"), limiter)

    message = make_message("https://a.edu/y")
    await handle_text(message, limiter)

    message.answer.assert_awaited_once()
    assert "Слишком много запросов" in message.answer.await_args.args[0]


async def test_text_without_url_does_not_use_up_the_limit() -> None:
    limiter = RateLimiter(limit=1)
    await handle_text(make_message("привет"), limiter)

    message = make_message("https://a.edu/x")
    await handle_text(message, limiter)
    assert message.answer.await_args_list[0].args[0] == messages.STATUS_READING


async def test_status_message_skips_identical_updates() -> None:
    message = make_message("x")
    status = StatusMessage(message)
    await status.update("a")
    await status.update("a")
    await status.update("b")

    message.answer.assert_awaited_once_with("a")
    message.answer.return_value.edit_text.assert_awaited_once_with("b")


async def test_status_message_survives_failed_edit() -> None:
    message = make_message("x")
    message.answer.return_value.edit_text = AsyncMock(
        side_effect=TelegramBadRequest(method=MagicMock(), message="message is not modified")
    )
    status = StatusMessage(message)
    await status.update("a")
    await status.update("b")  # must not raise


def test_dispatcher_routes_commands_before_catch_all() -> None:
    dp = build_dispatcher(RateLimiter(limit=10))
    names = [router.name for router in dp.sub_routers]
    assert names == ["start", "link"]
