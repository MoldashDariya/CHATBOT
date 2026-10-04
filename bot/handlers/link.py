import logging
from urllib.parse import urlsplit

from aiogram import F, Router
from aiogram.types import Message

from bot.config import Settings
from bot.handlers.flow import allow_request, reply_with_summary
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

    # Only real requests count against the limit, not /help or chatter.
    if not await allow_request(message, rate_limiter, language):
        return

    if len(urls) > 1:
        await message.answer(text(language, "multiple_urls"))

    url = urls[0]
    # Log only the host: full URLs may carry tokens in the query string.
    logger.info("Received link for host %s", urlsplit(url).hostname)

    async def job(on_stage: StageCallback) -> Summary:
        return await pipeline.run(url, language=language, on_stage=on_stage)

    status = StatusMessage(message)
    await status.update(text(language, "status_fetch"))
    await reply_with_summary(status, language=language, settings=settings, job=job)
