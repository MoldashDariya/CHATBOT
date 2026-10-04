import logging
from urllib.parse import urlsplit

from aiogram import F, Router
from aiogram.types import Message

from bot import messages
from bot.handlers.status import StatusMessage
from bot.urls import extract_urls

logger = logging.getLogger(__name__)

router = Router(name="link")


@router.message(F.text)
async def handle_text(message: Message) -> None:
    urls = extract_urls(message.text or "")
    if not urls:
        await message.answer(messages.NO_URL)
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
