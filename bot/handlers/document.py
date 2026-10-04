"""Files sent straight into the chat (PDF, DOCX). The bytes are the user's own, but the same
rules apply as for a link: size limit, time budget, format decided by content, never by name."""

import asyncio
import io
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Document, Message

from bot.config import Settings
from bot.errors import ContentTooLargeError, DownloadFailedError, ProcessingTimeoutError
from bot.handlers.flow import allow_request, reply_with_summary
from bot.handlers.status import StatusMessage
from bot.pipeline.orchestrator import Orchestrator, StageCallback
from bot.pipeline.schema import Summary
from bot.render.texts import text
from bot.security.limits import RateLimiter

logger = logging.getLogger(__name__)

router = Router(name="document")

DOWNLOAD_TIMEOUT_S = 60
_MAX_DISPLAY_NAME = 120


@router.message(F.document)
async def handle_document(
    message: Message,
    bot: Bot,
    rate_limiter: RateLimiter,
    pipeline: Orchestrator,
    settings: Settings,
) -> None:
    language = settings.default_lang
    document = message.document
    if document is None:  # cannot happen with F.document; keeps the types honest
        return
    if not await allow_request(message, rate_limiter, language):
        return

    limit = settings.max_download_mb * 1024 * 1024
    logger.info("Received a file (%s bytes, %s)", document.file_size, document.mime_type)

    async def job(on_stage: StageCallback) -> Summary:
        if document.file_size is not None and document.file_size > limit:
            raise ContentTooLargeError(f"declared {document.file_size} bytes")  # nothing downloaded
        body = await _download(bot, document)
        return await pipeline.run_file(
            body,
            filename=document.file_name or "document",
            content_type=document.mime_type or "",
            language=language,
            display_name=display_name(document.file_name),
            on_stage=on_stage,
        )

    status = StatusMessage(message)
    await status.update(text(language, "status_download"))
    await reply_with_summary(status, language=language, settings=settings, job=job)


@router.message(F.photo | F.video | F.audio | F.voice | F.video_note | F.sticker | F.animation)
async def handle_other_media(message: Message, settings: Settings) -> None:
    await message.answer(text(settings.default_lang, "unsupported_message"))


async def _download(bot: Bot, document: Document) -> bytes:
    buffer = io.BytesIO()
    try:
        async with asyncio.timeout(DOWNLOAD_TIMEOUT_S):
            await bot.download(document, destination=buffer, timeout=DOWNLOAD_TIMEOUT_S)
    except TimeoutError as exc:
        raise ProcessingTimeoutError("file download") from exc
    except TelegramAPIError as exc:
        if "too big" in str(exc).lower():  # Telegram lets bots fetch files up to 20 MB only
            raise ContentTooLargeError("Telegram refused: file is too big") from exc
        raise DownloadFailedError(type(exc).__name__) from exc
    return buffer.getvalue()


def display_name(file_name: str | None) -> str:
    """The file name as shown in the "Source" line: printable characters only, one line."""
    # Keep whitespace of any kind (a newline must separate words, not vanish) and drop the other
    # unprintable characters (escapes, zero-width and direction marks); then collapse to one line.
    kept = "".join(ch for ch in (file_name or "") if ch.isspace() or ch.isprintable())
    return " ".join(kept.split())[:_MAX_DISPLAY_NAME] or "file"
