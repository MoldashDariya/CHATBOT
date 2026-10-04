import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

logger = logging.getLogger(__name__)


class StatusMessage:
    """A single chat message that is edited in place as pipeline stages progress."""

    def __init__(self, message: Message) -> None:
        self._origin = message
        self._sent: Message | None = None
        self._last_text: str | None = None

    async def update(self, text: str) -> None:
        if text == self._last_text:
            return
        try:
            if self._sent is None:
                self._sent = await self._origin.answer(text)
            else:
                await self._sent.edit_text(text)
        except TelegramBadRequest:
            # Status updates are cosmetic: a failed edit must never break the pipeline.
            logger.warning("Could not update status message", exc_info=True)
            return
        self._last_text = text
