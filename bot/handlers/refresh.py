"""The "🔄 Обновить" button under an answer: read the page again, ignoring the stored answer."""

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.config import Settings
from bot.handlers.flow import limit_notice, reply_with_summary
from bot.handlers.status import StatusMessage
from bot.pipeline.orchestrator import Orchestrator, StageCallback
from bot.pipeline.schema import Summary
from bot.render.texts import text
from bot.security.limits import RateLimiter

logger = logging.getLogger(__name__)

router = Router(name="refresh")

PREFIX = "refresh:"  # callback data is "refresh:<16 hex>", well under Telegram's 64 bytes
_in_progress: set[str] = set()  # buttons being served, so a double tap starts one job, not two


async def refresh_keyboard(
    pipeline: Orchestrator, url: str, language: str
) -> InlineKeyboardMarkup | None:
    """The button for the answer just stored for `url`; None if nothing is stored (the cache is
    off or could not save it), because a button that leads nowhere is worse than none."""
    button_id = await pipeline.refresh_button_id(url, language)
    if button_id is None:
        return None
    button = InlineKeyboardButton(
        text=text(language, "button_refresh"), callback_data=f"{PREFIX}{button_id}"
    )
    return InlineKeyboardMarkup(inline_keyboard=[[button]])


@router.callback_query(F.data.startswith(PREFIX))
async def handle_refresh(
    callback: CallbackQuery, rate_limiter: RateLimiter, pipeline: Orchestrator, settings: Settings
) -> None:
    button_id = (callback.data or "").removeprefix(PREFIX)
    message = callback.message
    target = await pipeline.refresh_target(button_id)
    if target is None or not isinstance(message, Message):
        await callback.answer(text(settings.default_lang, "refresh_expired"), show_alert=True)
        return
    url, language = target

    if button_id in _in_progress:
        await callback.answer(text(language, "refresh_busy"))
        return
    # A refresh runs the whole pipeline again, so it counts like any other request.
    notice = limit_notice(rate_limiter, callback.from_user.id, language)
    if notice is not None:
        await callback.answer(notice, show_alert=True)
        return

    _in_progress.add(button_id)
    try:
        await callback.answer(text(language, "refresh_started"))
        logger.info("Refresh requested")

        async def job(on_stage: StageCallback) -> Summary:
            return await pipeline.run(url, language=language, on_stage=on_stage, refresh=True)

        async def keyboard() -> InlineKeyboardMarkup | None:
            return await refresh_keyboard(pipeline, url, language)

        status = StatusMessage(message)
        await status.update(text(language, "status_fetch"))
        delivered = await reply_with_summary(
            status, language=language, settings=settings, job=job, keyboard=keyboard
        )
        if delivered:  # the new card has its own button
            await _remove_buttons(message)
    finally:
        _in_progress.discard(button_id)


async def _remove_buttons(message: Message) -> None:
    try:
        await message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        logger.info("Old buttons could not be removed")  # e.g. the message is too old to edit
