"""
Основные команды бота: источники, расходы, аналитика.
"""
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from src.bot.keyboards.inline import (
    cancel_keyboard,
    confirm_delete_keyboard,
    costs_keyboard,
    sources_keyboard,
)
from src.services.analytics import (
    compute_metrics,
    format_source_detail,
    format_stats_table,
)
from src.services.attribution import (
    add_cost,
    create_source,
    delete_cost,
    delete_source,
    get_account_by_tg_id,
    get_costs,
    get_source_by_name,
    get_sources,
)
from src.services.health import check_bot_admin
from src.services.sheets_sync import setup_sheet_for_account, sync_to_sheets

logger = logging.getLogger(__name__)
router = Router()


# ─── /newsource ──────────────────────────────────────────────────────────────

@router.message(Command("newsource"))
async def cmd_newsource(message: Message, bot: Bot) -> None:
    try:
        account = await get_account_by_tg_id(message.from_user.id)
        if not account:
            await message.answer("Сначала запусти /start")
            return

        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.answer(
                "Использование: /newsource <i>имя_источника</i>\n"
                "Пример: <code>/newsource YouTube_видео_1</code>\n\n"
                "Имя может содержать буквы, цифры и _. Максимум 32 символа.",
                parse_mode="HTML",
            )
            return

        name = args[1].strip()
        if len(name) > 32:
            await message.answer(f"Имя слишком длинное ({len(name)} символов), максимум 32.")
            return
        if not name:
            await message.answer("Имя не может быть пустым.")
            return

        free_channel_id = account.get("free_channel_id")
        if not free_channel_id:
            await message.answer("Сначала привяжи бесплатный канал: /setchannel")
            return

        existing = await get_source_by_name(account["id"], name)
        if existing:
            await message.answer(
                f"Источник с именем <b>{name}</b> уже существует.\n"
                "Посмотри список: /sources",
                parse_mode="HTML",
            )
            return

        wait_msg = await message.answer("⏳ Создаю ссылку...")

        try:
            result = await bot.create_chat_invite_link(
                chat_id=free_channel_id,
                name=name,
                creates_join_request=False,
            )
            invite_link = result.invite_link
            invite_name = result.name or name
        except TelegramAPIError as e:
            err = str(e)
            if "429" in err or "retry" in err.lower():
                await wait_msg.edit_text("⏳ Telegram перегружен, попробуй через минуту.")
            elif "not enough rights" in err.lower() or "chat_admin_required" in err.lower():
                await wait_msg.edit_text(
                    "⚠️ У бота нет права создавать ссылки.\n\n"
                    "Открой настройки канала → Администраторы → найди бота → "
                    "включи галочку <b>«Добавление участников»</b>."
                )
            else:
                logger.error("Ошибка create_chat_invite_link: %s", e)
                await wait_msg.edit_text(f"❌ Ошибка Telegram: {e}")
            return

        await create_source(
            account_id=account["id"],
            name=name,
            invite_link=invite_link,
            invite_name=invite_name,
        )

        await wait_msg.edit_text(
            f"✅ Источник создан!\n\n"
            f"📛 Имя: <b>{name}</b>\n"
            f"🔗 Ссылка:\n<code>{invite_link}</code>\n\n"
            f"Используй эту ссылку в рекламе — бот автоматически засчитает источник.",
            parse_mode="HTML",
        )

        # Автоматически обновляем таблицу
        from src.services.sheets_sync import sync_to_sheets
        await sync_to_sheets(account["id"])

    except Exception as e:
        logger.error("Необработанная ошибка в /newsource: %s", e, exc_info=True)
        await message.answer(f"❌ Внутренняя ошибка: {e}\n\nПроверь логи Railway.")


# ─── /sources ─────────────────────────────────────────────────────────────────

@router.message(Command("sources"))
async def cmd_sources(message: Message) -> None:
    account = await get_account_by_tg_id(message.from_user.id)
    if not account:
        await message.answer("Сначала запусти /start")
        return

    sources = await get_sources(account["id"])
    if not sources:
        await message.answer(
            "Источников нет. Создай первый:\n"
            "<code>/newsource имя_источника</code>",
            parse_mode="HTML",
        )
        return

    text = f"<b>📋 Источники ({len(sources)})</b>\n\n"
    for i, src in enumerate(sources, 1):
        text += f"{i}. <b>{src['name']}</b>\n<code>{src['invite_link']}</code>\n\n"

    await message.answer(text, parse_mode="HTML", reply_markup=sources_keyboard(sources))


# ─── Callbacks для источников ─────────────────────────────────────────────────

@router.callback_query(F.data.startswith("src_info:"))
async def cb_source_info(callback: CallbackQuery) -> None:
    source_id = callback.data.split(":", 1)[1]
    account = await get_account_by_tg_id(callback.from_user.id)
    if not account:
        await callback.answer("Не найден аккаунт", show_alert=True)
        return
    sources = await get_sources(account["id"])
    src = next((s for s in sources if s["id"] == source_id), None)
    if not src:
        await callback.answer("Источник не найден", show_alert=True)
        return
    await callback.message.answer(
        f"<b>{src['name']}</b>\n🔗 <code>{src['invite_link']}</code>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("src_stats:"))
async def cb_source_stats(callback: CallbackQuery) -> None:
    source_id = callback.data.split(":", 1)[1]
    account = await get_account_by_tg_id(callback.from_user.id)
    if not account:
        await callback.answer("Не найден аккаунт", show_alert=True)
        return
    sources = await get_sources(account["id"])
    src = next((s for s in sources if s["id"] == source_id), None)
    if not src:
        await callback.answer("Источник не найден", show_alert=True)
        return

    metrics = await compute_metrics(account["id"])
    m = next((x for x in metrics if x.source_id == source_id), None)
    if not m:
        await callback.message.answer("Нет данных по этому источнику.")
    else:
        await callback.message.answer(format_source_detail(m), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data.startswith("src_delete:"))
async def cb_source_delete(callback: CallbackQuery) -> None:
    source_id = callback.data.split(":", 1)[1]
    await callback.message.answer(
        "⚠️ Удалить источник? Все данные (подписчики, расходы) по нему будут удалены.",
        reply_markup=confirm_delete_keyboard(source_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("src_delete_confirm:"))
async def cb_source_delete_confirm(callback: CallbackQuery) -> None:
    source_id = callback.data.split(":", 1)[1]
    await delete_source(source_id)
    await callback.message.edit_text("✅ Источник удалён.")
    await callback.answer()


@router.callback_query(F.data == "src_delete_cancel")
async def cb_source_delete_cancel(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Отменено.")
    await callback.answer()


@router.callback_query(F.data == "src_new")
async def cb_source_new(callback: CallbackQuery) -> None:
    await callback.message.answer(
        "Создай источник командой:\n<code>/newsource имя_источника</code>",
        parse_mode="HTML",
    )
    await callback.answer()


# ─── /importlinks ─────────────────────────────────────────────────────────────

@router.message(Command("importlinks"))
async def cmd_importlinks(message: Message, bot: Bot) -> None:
    account = await get_account_by_tg_id(message.from_user.id)
    if not account:
        await message.answer("Сначала запусти /start")
        return

    free_channel_id = account.get("free_channel_id")
    if not free_channel_id:
        await message.answer("Сначала привяжи бесплатный канал: /setchannel")
        return

    await message.answer(
        "📥 <b>Импорт ссылок</b>\n\n"
        "Отправь список invite-ссылок одним сообщением — каждая с новой строки.\n"
        "Формат: <code>имя_источника: https://t.me/+xxx</code>\n\n"
        "Пример:\n"
        "<code>YouTube_видео_1: https://t.me/+abc123\n"
        "Посев_канал_X: https://t.me/+def456</code>",
        parse_mode="HTML",
    )


# ─── /cost ────────────────────────────────────────────────────────────────────

@router.message(Command("cost"))
async def cmd_cost(message: Message) -> None:
    account = await get_account_by_tg_id(message.from_user.id)
    if not account:
        await message.answer("Сначала запусти /start")
        return

    parts = message.text.split(maxsplit=3)
    if len(parts) < 3:
        await message.answer(
            "Использование: /cost <i>источник</i> <i>сумма</i> [<i>заметка</i>]\n"
            "Пример: <code>/cost YouTube_видео_1 60000 январь</code>",
            parse_mode="HTML",
        )
        return

    source_name = parts[1]
    amount_str = parts[2]
    note = parts[3] if len(parts) > 3 else None

    try:
        amount = float(amount_str.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Сумма должна быть положительным числом.")
        return

    source = await get_source_by_name(account["id"], source_name)
    if not source:
        sources = await get_sources(account["id"])
        names = ", ".join(s["name"] for s in sources) if sources else "нет источников"
        await message.answer(
            f"Источник <b>{source_name}</b> не найден.\n"
            f"Доступные: {names}\n"
            "Посмотри /sources",
            parse_mode="HTML",
        )
        return

    await add_cost(
        account_id=account["id"],
        source_id=source["id"],
        amount=amount,
        note=note,
    )
    await message.answer(
        f"✅ Расход добавлен:\n"
        f"Источник: <b>{source_name}</b>\n"
        f"Сумма: <b>{amount:.0f} ₽</b>"
        + (f"\nЗаметка: {note}" if note else ""),
        parse_mode="HTML",
    )

    from src.services.sheets_sync import sync_to_sheets
    await sync_to_sheets(account["id"])


# ─── /costs ───────────────────────────────────────────────────────────────────

@router.message(Command("costs"))
async def cmd_costs(message: Message) -> None:
    account = await get_account_by_tg_id(message.from_user.id)
    if not account:
        await message.answer("Сначала запусти /start")
        return

    costs = await get_costs(account["id"])
    if not costs:
        await message.answer(
            "Расходов нет.\n"
            "Добавить: <code>/cost источник сумма</code>",
            parse_mode="HTML",
        )
        return

    total = sum(float(c["amount"]) for c in costs)
    text = f"<b>💸 Расходы (всего: {total:.0f} ₽)</b>\n\n"
    for c in costs:
        src_name = c.get("sources", {}).get("name", "?") if c.get("sources") else "?"
        note = f" ({c['note']})" if c.get("note") else ""
        text += f"• <b>{src_name}</b>: {float(c['amount']):.0f} ₽{note}\n"

    await message.answer(text, parse_mode="HTML", reply_markup=costs_keyboard(costs))


# ─── Callbacks для расходов ───────────────────────────────────────────────────

@router.callback_query(F.data.startswith("cost_delete:"))
async def cb_cost_delete(callback: CallbackQuery) -> None:
    cost_id = callback.data.split(":", 1)[1]
    await delete_cost(cost_id)
    await callback.answer("Расход удалён ✅")
    await callback.message.edit_text("Расход удалён.")


# ─── /stats ───────────────────────────────────────────────────────────────────

@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    account = await get_account_by_tg_id(message.from_user.id)
    if not account:
        await message.answer("Сначала запусти /start")
        return

    args = message.text.split(maxsplit=1)
    source_filter = args[1].strip() if len(args) > 1 else None

    wait_msg = await message.answer("⏳ Считаю метрики...")

    try:
        metrics = await compute_metrics(account["id"])
    except Exception as e:
        logger.error("Ошибка compute_metrics: %s", e, exc_info=True)
        await wait_msg.edit_text("Ошибка при расчёте метрик. Проверь что миграция Supabase применена (/start для диагностики).")
        return

    if not metrics:
        await wait_msg.edit_text("Нет данных. Добавь источники (/newsource) и дождись подписчиков.")
        return

    if source_filter:
        m = next((x for x in metrics if x.name.lower() == source_filter.lower()), None)
        if not m:
            names = ", ".join(x.name for x in metrics)
            await wait_msg.edit_text(
                f"Источник <b>{source_filter}</b> не найден.\nДоступные: {names}",
                parse_mode="HTML",
            )
            return
        await wait_msg.edit_text(format_source_detail(m), parse_mode="HTML")
    else:
        await wait_msg.edit_text(format_stats_table(metrics), parse_mode="HTML")


# ─── /syncsheets ─────────────────────────────────────────────────────────────

@router.message(Command("syncsheets"))
async def cmd_syncsheets(message: Message) -> None:
    account = await get_account_by_tg_id(message.from_user.id)
    if not account:
        await message.answer("Сначала запусти /start")
        return

    wait_msg = await message.answer("⏳ Синхронизирую с Google Sheets...")

    ok = await setup_sheet_for_account(account["id"])
    if not ok:
        await wait_msg.edit_text(
            "Ошибка подключения к Google Sheets.\n"
            "Проверь, что переменные GOOGLE_CREDENTIALS_JSON и GOOGLE_SHEET_ID заданы, "
            "и таблица расшарена для сервис-аккаунта."
        )
        return

    await sync_to_sheets(account["id"])
    await wait_msg.edit_text("✅ Синхронизация выполнена! Открой таблицу чтобы увидеть данные.")
