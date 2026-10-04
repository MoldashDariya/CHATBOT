"""What links and uploaded files have in common: the rate limit, the progress message, the card
or the friendly error at the end."""

import logging
import math
from collections.abc import Awaitable, Callable

from aiogram.types import InlineKeyboardMarkup, Message

from bot.config import Settings
from bot.handlers.status import StatusMessage
from bot.pipeline.orchestrator import Stage, StageCallback
from bot.pipeline.schema import Summary
from bot.render.errors import handle_error
from bot.render.telegram import render_summary
from bot.render.texts import text
from bot.security.limits import RateLimiter

logger = logging.getLogger(__name__)

_STAGE_TEXT = {
    Stage.FETCHING: "status_fetch",
    Stage.READING: "status_read",
    Stage.ANALYZING: "status_analyze",
    Stage.FOLLOWING: "status_follow",
}

Job = Callable[[StageCallback], Awaitable[Summary]]
Keyboard = Callable[[], Awaitable[InlineKeyboardMarkup | None]]


def limit_notice(rate_limiter: RateLimiter, user_id: int, language: str) -> str | None:
    """Count one request against the user's hourly limit. None if it is allowed, otherwise the
    text that tells them how long to wait."""
    decision = rate_limiter.acquire(user_id)
    if decision.allowed:
        return None
    minutes = max(1, math.ceil(decision.retry_after_s / 60))
    return text(language, "rate_limited", minutes=minutes)


async def allow_request(message: Message, rate_limiter: RateLimiter, language: str) -> bool:
    """Count this request against the user's hourly limit, or tell them to wait."""
    user_id = message.from_user.id if message.from_user else message.chat.id
    notice = limit_notice(rate_limiter, user_id, language)
    if notice is None:
        return True
    await message.answer(notice)
    return False


async def reply_with_summary(
    status: StatusMessage,
    *,
    language: str,
    settings: Settings,
    job: Job,
    keyboard: Keyboard | None = None,
) -> bool:
    """Run `job` while the status message follows its stages, then show the card (with the
    buttons `keyboard` builds, if any). Any failure, including Telegram refusing to deliver the
    card, ends as a friendly message. True if the card was delivered."""
    try:
        summary = await job(_show_progress(status, language))
        await status.finish(render_summary(summary), await _buttons(keyboard))
    except Exception as exc:
        message = handle_error(exc, language, max_download_mb=settings.max_download_mb)
        await _tell_user(status, message)
        return False
    return True


async def _buttons(keyboard: Keyboard | None) -> InlineKeyboardMarkup | None:
    """Buttons are a convenience: failing to build them must not cost the user their card."""
    if keyboard is None:
        return None
    try:
        return await keyboard()
    except Exception:
        logger.warning("Could not build the buttons", exc_info=True)
        return None


def _show_progress(status: StatusMessage, language: str) -> StageCallback:
    async def show(stage: Stage) -> None:
        await status.update(text(language, _STAGE_TEXT[stage]))

    return show


async def _tell_user(status: StatusMessage, message: str) -> None:
    try:
        await status.update(message)
    except Exception:
        logger.exception("Could not even report the failure to the user")
