"""
Точка входа. Запуск: python -m src.main
Режим: webhook (для продакшна на Railway).
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.bot.handlers import setup_handlers
from src.core.config import config
from src.core.logging_setup import setup_logging

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/webhook"
HEALTH_PATH = "/health"

ALLOWED_UPDATES = [
    "message",
    "callback_query",
    "chat_member",
    "chat_join_request",
    "my_chat_member",
]


async def on_startup(bot: Bot) -> None:
    me = await bot.get_me()
    logger.info("Бот запущен: @%s (id=%s)", me.username, me.id)

    if config.webhook_url:
        webhook_url = f"{config.webhook_url}{WEBHOOK_PATH}"
        await bot.set_webhook(
            url=webhook_url,
            allowed_updates=ALLOWED_UPDATES,
            drop_pending_updates=False,
        )
        logger.info("Webhook установлен: %s", webhook_url)
    else:
        logger.warning("WEBHOOK_URL не задан — webhook не установлен")


async def on_shutdown(bot: Bot) -> None:
    logger.info("Бот завершает работу...")
    if config.webhook_url:
        await bot.delete_webhook()


async def health_handler(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def _run_scheduled_sync(bot: Bot) -> None:
    """Cron-задача: синк с Google Sheets для всех аккаунтов с enabled sync."""
    try:
        from src.core.database import get_db, run_sync
        from src.services.sheets_sync import sync_to_sheets

        def _get_accounts_with_sync():
            db = get_db()
            result = db.table("settings").select("account_id").eq("sync_enabled", True).execute()
            return result.data or []

        accounts = await run_sync(_get_accounts_with_sync)
        for row in accounts:
            account_id = row["account_id"]
            logger.info("Плановый синк для account=%s", account_id)
            await sync_to_sheets(account_id)
    except Exception as e:
        logger.error("Ошибка планового синка: %s", e, exc_info=True)


def main() -> None:
    setup_logging()

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    setup_handlers(dp)

    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    app = web.Application()
    app.router.add_get(HEALTH_PATH, health_handler)

    webhook_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_handler.register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    # APScheduler: синк каждый час
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_scheduled_sync,
        trigger="interval",
        minutes=60,
        kwargs={"bot": bot},
        id="sheets_sync",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("APScheduler запущен (синк каждые 60 мин)")

    logger.info("Старт на порту %s", config.webhook_port)
    web.run_app(app, host="0.0.0.0", port=config.webhook_port)


if __name__ == "__main__":
    main()
