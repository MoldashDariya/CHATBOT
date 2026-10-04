import logging
import math
from urllib.parse import urlsplit

from aiogram import F, Router
from aiogram.types import Message

from bot.config import Settings
from bot.handlers.status import StatusMessage
from bot.pipeline.orchestrator import Orchestrator, Stage, StageCallback
from bot.render.errors import handle_error
from bot.render.telegram import render_summary
from bot.render.texts import text
from bot.security.limits import RateLimiter
from bot.urls import extract_urls

logger = logging.getLogger(__name__)

router = Router(name="link")

_STAGE_TEXT = {
    Stage.FETCHING: "status_fetch",
    Stage.READING: "status_read",
    Stage.ANALYZING: "status_analyze",
}


@router.message(F.text)
async def handle_text(
    message: Message, rate_limiter: RateLimiter, pipeline: Orchestrator, settings: Settings
) -> None:
    language = settings.default_lang  # per-user choice arrives with /lang (task 2.4)

    urls = extract_urls(message.text or "")
    if not urls:
        await message.answer(text(language, "no_url"))
        return

    # Only real link requests count against the limit, not /help or chatter.
    user_id = message.from_user.id if message.from_user else message.chat.id
    decision = rate_limiter.acquire(user_id)
    if not decision.allowed:
        minutes = max(1, math.ceil(decision.retry_after_s / 60))
        await message.answer(text(language, "rate_limited", minutes=minutes))
        return

    if len(urls) > 1:
        await message.answer(text(language, "multiple_urls"))

    url = urls[0]
    # Log only the host: full URLs may carry tokens in the query string.
    logger.info("Received link for host %s", urlsplit(url).hostname)

    status = StatusMessage(message)
    await status.update(text(language, "status_fetch"))
    try:
        summary = await pipeline.run(
            url, language=language, on_stage=_show_progress(status, language)
        )
        await status.finish(render_summary(summary))
    except Exception as exc:
        # Includes Telegram refusing to deliver the card: the user still gets an answer.
        await _tell_user(
            status, handle_error(exc, language, max_download_mb=settings.max_download_mb)
        )


def _show_progress(status: StatusMessage, language: str) -> StageCallback:
    async def show(stage: Stage) -> None:
        await status.update(text(language, _STAGE_TEXT[stage]))

    return show


async def _tell_user(status: StatusMessage, message: str) -> None:
    try:
        await status.update(message)
    except Exception:
        logger.exception("Could not even report the failure to the user")
