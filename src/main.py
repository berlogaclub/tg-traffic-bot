"""
Точка входа. Запуск: python -m src.main
Режим: webhook если задан WEBHOOK_URL, иначе polling.
"""
import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ErrorEvent
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


def _get_port() -> int:
    return int(os.environ.get("PORT", os.environ.get("WEBHOOK_PORT", "8080")))


async def _run_scheduled_sync(bot: Bot) -> None:
    try:
        from src.core.database import get_db, run_sync
        from src.services.sheets_sync import sync_to_sheets

        def _get_accounts():
            db = get_db()
            return (
                db.table("settings")
                .select("account_id")
                .eq("sync_enabled", True)
                .execute()
                .data
                or []
            )

        accounts = await run_sync(_get_accounts)
        for row in accounts:
            ok, msg = await sync_to_sheets(row["account_id"])
            if not ok:
                logger.error("Плановый синк account=%s: %s", row["account_id"], msg)
    except Exception as e:
        logger.error("Ошибка планового синка: %s", e, exc_info=True)


def _start_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _run_scheduled_sync,
        trigger="interval",
        minutes=10,
        kwargs={"bot": bot},
        id="sheets_sync",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("APScheduler запущен (синк каждые 10 мин)")
    return scheduler


async def _run_webhook(bot: Bot, dp: Dispatcher) -> None:
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
    from aiohttp import web

    webhook_url = f"{config.webhook_url}{WEBHOOK_PATH}"
    await bot.set_webhook(
        url=webhook_url,
        allowed_updates=ALLOWED_UPDATES,
        drop_pending_updates=True,
    )
    logger.info("Webhook установлен: %s", webhook_url)

    async def health_handler(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    app = web.Application()
    app.router.add_get(HEALTH_PATH, health_handler)

    SimpleRequestHandler(dispatcher=dp, bot=bot).register(app, path=WEBHOOK_PATH)
    setup_application(app, dp, bot=bot)

    port = _get_port()
    logger.info("Старт webhook на порту %s", port)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        try:
            await bot.delete_webhook()
        except Exception:
            pass


async def _run_polling(bot: Bot, dp: Dispatcher) -> None:
    logger.info("Старт в режиме polling")
    await bot.delete_webhook(drop_pending_updates=True)

    while True:
        try:
            await dp.start_polling(
                bot,
                allowed_updates=ALLOWED_UPDATES,
                handle_signals=False,
            )
        except Exception as e:
            logger.error("Polling упал: %s — перезапуск через 5 сек", e, exc_info=True)
            await asyncio.sleep(5)


async def _main_async() -> None:
    setup_logging()

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    setup_handlers(dp)

    @dp.errors()
    async def global_error_handler(event: ErrorEvent) -> bool:
        logger.error(
            "Необработанная ошибка в хендлере: %s",
            event.exception,
            exc_info=event.exception,
        )
        return True  # подавляем ошибку, polling продолжается

    me = await bot.get_me()
    logger.info("Бот: @%s (id=%s)", me.username, me.id)

    _start_scheduler(bot)

    if config.webhook_url:
        await _run_webhook(bot, dp)
    else:
        await _run_polling(bot, dp)


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
