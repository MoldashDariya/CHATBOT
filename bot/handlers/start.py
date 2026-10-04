from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from bot import messages

router = Router(name="start")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer(messages.START)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(messages.HELP, parse_mode=ParseMode.HTML)
