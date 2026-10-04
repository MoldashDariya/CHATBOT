import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from bot.config import get_settings
from bot.handlers import link, start
from bot.log import setup_logging

logger = logging.getLogger(__name__)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    # Order matters: commands must be matched before the catch-all text handler.
    dp.include_router(start.router)
    dp.include_router(link.router)
    return dp


async def run() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    bot = Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(link_preview_is_disabled=True),
    )
    dp = build_dispatcher()
    try:
        me = await bot.get_me()
        logger.info("Starting bot @%s", me.username)
        # aiogram's polling installs SIGINT/SIGTERM handlers and stops cleanly on them.
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("Bot stopped")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
