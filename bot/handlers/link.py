import logging
from urllib.parse import urlsplit

from aiogram import F, Router
from aiogram.types import InlineKeyboardMarkup, Message

from bot.config import Settings
from bot.handlers.flow import allow_request, reply_with_summary
from bot.handlers.refresh import refresh_keyboard
from bot.handlers.status import StatusMessage
from bot.pipeline.orchestrator import Orchestrator, StageCallback
from bot.pipeline.schema import Summary
from bot.render.texts import text
from bot.security.limits import RateLimiter
from bot.urls import extract_urls

logger = logging.getLogger(__name__)

router = Router(name="link")


@router.message(F.text)
async def handle_text(
    message: Message, rate_limiter: RateLimiter, pipeline: Orchestrator, settings: Settings
) -> None:
    language = settings.default_lang  # per-user choice arrives with /lang (task 2.4)

    urls = extract_urls(message.text or "")
    if not urls:
        await message.answer(text(language, "no_url"))
        return

    url = urls[0]
    # A stored answer costs nothing, so it does not count against the hourly limit (only real,
    # expensive requests do, not /help, chatter or a repeated link).
    stored = await pipeline.cached_summary(url, language)
    if stored is None and not await allow_request(message, rate_limiter, language):
        return

    if len(urls) > 1:
        await message.answer(text(language, "multiple_urls"))

    # Log only the host: full URLs may carry tokens in the query string.
    logger.info(
        "Received link for host %s (stored answer: %s)", urlsplit(url).hostname, bool(stored)
    )

    async def job(on_stage: StageCallback) -> Summary:
        if stored is not None:
            return stored
        return await pipeline.run(url, language=language, on_stage=on_stage)

    async def keyboard() -> InlineKeyboardMarkup | None:
        return await refresh_keyboard(pipeline, url, language)

    status = StatusMessage(message)
    if stored is None:  # a stored answer needs no "reading..." message
        await status.update(text(language, "status_fetch"))
    await reply_with_summary(
        status, language=language, settings=settings, job=job, keyboard=keyboard
    )
