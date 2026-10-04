import html
import logging
import re

from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

logger = logging.getLogger(__name__)

_ANCHOR_RE = re.compile(r'<a href="([^"]*)">(.*?)</a>', re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def to_plain_text(markup: str) -> str:
    """The card without markup, for the case Telegram refuses to parse it. Links keep their
    address: `<a href="U">text</a>` becomes `text (U)`."""

    def anchor(match: re.Match[str]) -> str:
        href, label = html.unescape(match.group(1)), html.unescape(_TAG_RE.sub("", match.group(2)))
        return label if label == href else f"{label} ({href})"

    return html.unescape(_TAG_RE.sub("", _ANCHOR_RE.sub(anchor, markup)))


class StatusMessage:
    """One chat message that is edited in place as the pipeline progresses and finally becomes
    the first message of the answer."""

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

    async def finish(self, cards: list[str]) -> None:
        """Show the answer: the first card replaces the status message, the rest follow as new
        messages. Cards are HTML; if Telegram will not parse one it goes out as plain text."""
        first, *rest = cards
        await self._deliver(first, replace_status=True)
        for card in rest:
            await self._deliver(card, replace_status=False)

    async def _deliver(self, markup: str, *, replace_status: bool) -> None:
        attempts: list[tuple[bool, str | None]] = []  # (edit the status message?, parse mode)
        for edit in (True, False) if replace_status and self._sent is not None else (False,):
            attempts += [(edit, ParseMode.HTML), (edit, None)]

        last_error: TelegramBadRequest | None = None
        for edit, parse_mode in attempts:
            body = markup if parse_mode else to_plain_text(markup)
            try:
                if edit and self._sent is not None:
                    await self._sent.edit_text(body, parse_mode=parse_mode)
                else:
                    await self._origin.answer(body, parse_mode=parse_mode)
                return
            except TelegramBadRequest as exc:
                last_error = exc
                logger.warning(
                    "Telegram rejected a card (edit=%s, html=%s): %s",
                    edit,
                    parse_mode is not None,
                    exc.message,
                )
        assert last_error is not None
        raise last_error
