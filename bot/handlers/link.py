import logging
from urllib.parse import urlsplit

from aiogram import F, Router
from aiogram.types import Message

from bot import messages
from bot.handlers.status import StatusMessage
from bot.security.limits import RateLimiter
from bot.urls import extract_urls

logger = logging.getLogger(__name__)

router = Router(name="link")


@router.message(F.text)
async def handle_text(message: Message, rate_limiter: RateLimiter) -> None:
    urls = extract_urls(message.text or "")
    if not urls:
        await message.answer(messages.NO_URL)
        return

    # Only real link requests count against the limit, not /help or chatter.
    user_id = message.from_user.id if message.from_user else message.chat.id
    decision = rate_limiter.acquire(user_id)
    if not decision.allowed:
        await message.answer(messages.rate_limited(decision.retry_after_s))
        return

    if len(urls) > 1:
        await message.answer(messages.MULTIPLE_URLS)

    url = urls[0]
    # Log only the host: full URLs may carry tokens in the query string.
    logger.info("Received link for host %s", urlsplit(url).hostname)

    status = StatusMessage(message)
    await status.update(messages.STATUS_READING)
    # The pipeline (detect -> fetch -> extract -> summarize) is wired in at task 1.29.
    await status.update(messages.NOT_READY)
