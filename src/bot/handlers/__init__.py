from aiogram import Dispatcher

from src.bot.handlers.chat_member import router as chat_member_router
from src.bot.handlers.setup import router as setup_router
from src.bot.handlers.commands import router as commands_router


def setup_handlers(dp: Dispatcher) -> None:
    dp.include_router(chat_member_router)
    dp.include_router(setup_router)
    dp.include_router(commands_router)
