"""What links and uploaded files have in common: the rate limit, the progress message, the card
or the friendly error at the end."""

import logging
import math
from collections.abc import Awaitable, Callable

from aiogram.types import Message

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
}

Job = Callable[[StageCallback], Awaitable[Summary]]


async def allow_request(message: Message, rate_limiter: RateLimiter, language: str) -> bool:
    """Count this request against the user's hourly limit, or tell them to wait."""
    user_id = message.from_user.id if message.from_user else message.chat.id
    decision = rate_limiter.acquire(user_id)
    if decision.allowed:
        return True
    minutes = max(1, math.ceil(decision.retry_after_s / 60))
    await message.answer(text(language, "rate_limited", minutes=minutes))
    return False


async def reply_with_summary(
    status: StatusMessage, *, language: str, settings: Settings, job: Job
) -> None:
    """Run `job` while the status message follows its stages, then show the card. Any failure,
    including Telegram refusing to deliver the card, ends as a friendly message."""
    try:
        summary = await job(_show_progress(status, language))
        await status.finish(render_summary(summary))
    except Exception as exc:
        message = handle_error(exc, language, max_download_mb=settings.max_download_mb)
        await _tell_user(status, message)


def _show_progress(status: StatusMessage, language: str) -> StageCallback:
    async def show(stage: Stage) -> None:
        await status.update(text(language, _STAGE_TEXT[stage]))

    return show


async def _tell_user(status: StatusMessage, message: str) -> None:
    try:
        await status.update(message)
    except Exception:
        logger.exception("Could not even report the failure to the user")
