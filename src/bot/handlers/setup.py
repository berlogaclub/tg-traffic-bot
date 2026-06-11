"""
Команды онбординга: /setchannel, /setpaid, /swap, /start.
FSM для привязки каналов.
"""
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from src.bot.keyboards.inline import cancel_keyboard, setup_keyboard
from src.services.attribution import (
    get_account_by_tg_id,
    get_or_create_account,
    update_account,
)
from src.services.health import check_bot_admin, get_status_text

logger = logging.getLogger(__name__)
router = Router()


class SetupStates(StatesGroup):
    waiting_for_channel = State()
    waiting_for_paid_chat = State()
    waiting_for_price = State()


# ─── /start ──────────────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    try:
        account = await get_or_create_account(message.from_user.id)
    except Exception as e:
        logger.error("Ошибка /start (БД): %s", e, exc_info=True)
        await message.answer(
            "⚠️ Ошибка подключения к базе данных.\n\n"
            "Возможные причины:\n"
            "• Миграция Supabase не применена (001_initial.sql)\n"
            "• Неверные SUPABASE_URL или SUPABASE_SERVICE_KEY в Railway Variables\n\n"
            "Исправь и попробуй снова."
        )
        return

    text = (
        "👋 <b>TG Traffic Analytics</b>\n\n"
        "Я слежу за источниками трафика и связываю подписчиков с покупателями.\n\n"
        "Для начала работы:\n"
        "1. Нажми <b>«Привязать канал»</b> — твой бесплатный канал\n"
        "2. Нажми <b>«Привязать чат»</b> — твой платный чат\n"
        "3. Добавь меня администратором в оба чата\n"
        "4. Создай первый источник: /newsource <i>имя</i>\n\n"
        "Все команды: /help"
    )
    await message.answer(text, parse_mode="HTML", reply_markup=setup_keyboard())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    text = (
        "<b>Команды бота:</b>\n\n"
        "<b>Настройка</b>\n"
        "/start — начало работы\n"
        "/setchannel — привязать бесплатный канал\n"
        "/setpaid — привязать платный чат\n"
        "/swap — поменять канал и чат местами\n"
        "/status — статус бота и прав\n\n"
        "<b>Источники</b>\n"
        "/newsource &lt;имя&gt; — создать источник + ссылку\n"
        "/sources — список источников\n"
        "/importlinks — импорт ссылок из канала\n\n"
        "<b>Расходы</b>\n"
        "/cost &lt;источник&gt; &lt;сумма&gt; — добавить расход\n"
        "/costs — все расходы\n\n"
        "<b>Аналитика</b>\n"
        "/stats — дашборд по всем источникам\n"
        "/stats &lt;источник&gt; — детализация одного источника\n"
        "/setprice &lt;сумма&gt; — установить цену продукта\n"
        "/syncsheets — синхронизировать с Google Sheets"
    )
    await message.answer(text, parse_mode="HTML")


# ─── /status ─────────────────────────────────────────────────────────────────

@router.message(Command("status"))
async def cmd_status(message: Message, bot: Bot) -> None:
    account = await get_account_by_tg_id(message.from_user.id)
    if not account:
        await message.answer("Сначала запусти /start")
        return
    text = await get_status_text(bot, account)
    await message.answer(text, parse_mode="HTML")


# ─── Callback-кнопки онбординга ───────────────────────────────────────────────

@router.callback_query(F.data == "setup_channel")
async def cb_setup_channel(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.answer(
        "📢 Отправь ID бесплатного канала или перешли любое сообщение из него.\n\n"
        "ID канала выглядит так: <code>-1001234567890</code>\n"
        "Узнать ID: добавь бота @userinfobot в канал, он пришлёт ID.",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await state.set_state(SetupStates.waiting_for_channel)
    await callback.answer()


@router.callback_query(F.data == "setup_paid")
async def cb_setup_paid(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.answer(
        "🔐 Отправь ID платного чата или перешли любое сообщение из него.",
        reply_markup=cancel_keyboard(),
    )
    await state.set_state(SetupStates.waiting_for_paid_chat)
    await callback.answer()


@router.callback_query(F.data == "setup_status")
async def cb_setup_status(callback: CallbackQuery, bot: Bot) -> None:
    account = await get_account_by_tg_id(callback.from_user.id)
    if not account:
        await callback.answer("Сначала запусти /start", show_alert=True)
        return
    text = await get_status_text(bot, account)
    await callback.message.answer(text, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("Отменено.")
    await callback.answer()


# ─── /setchannel ─────────────────────────────────────────────────────────────

@router.message(Command("setchannel"))
async def cmd_setchannel(message: Message, state: FSMContext) -> None:
    await message.answer(
        "📢 Отправь ID бесплатного канала или перешли любое сообщение из него.",
        reply_markup=cancel_keyboard(),
    )
    await state.set_state(SetupStates.waiting_for_channel)


@router.message(SetupStates.waiting_for_channel)
async def process_channel_input(message: Message, state: FSMContext, bot: Bot) -> None:
    channel_id = _extract_chat_id(message)
    if not channel_id:
        await message.answer(
            "Не удалось определить ID канала. Отправь числовой ID (например <code>-1001234567890</code>) "
            "или перешли сообщение из канала.\n\n"
            "💡 Узнать ID: добавь @userinfobot в канал, он пришлёт ID.",
            parse_mode="HTML",
        )
        return

    account = await get_account_by_tg_id(message.from_user.id)
    if not account:
        account = await get_or_create_account(message.from_user.id)

    await update_account(account["id"], {"free_channel_id": channel_id})
    await state.clear()

    is_admin, can_invite = await check_bot_admin(bot, channel_id)

    if not is_admin:
        await message.answer(
            f"✅ Бесплатный канал привязан: <code>{channel_id}</code>\n\n"
            "⚠️ Бот пока не видит себя администратором — добавь его как администратора с правом «Добавление участников».\n"
            "После добавления проверь /status.\n\n"
            "Привяжи платный чат: /setpaid",
            parse_mode="HTML",
        )
        return

    invite_warn = ""
    if not can_invite:
        invite_warn = "\n\n⚠️ У бота нет права <b>«Добавление участников»</b> — команда /newsource не будет работать. Выдай это право в настройках канала."

    await message.answer(
        f"✅ Бесплатный канал привязан: <code>{channel_id}</code>{invite_warn}\n\n"
        "Теперь привяжи платный чат: /setpaid",
        parse_mode="HTML",
    )


# ─── /setpaid ────────────────────────────────────────────────────────────────

@router.message(Command("setpaid"))
async def cmd_setpaid(message: Message, state: FSMContext) -> None:
    await message.answer(
        "🔐 Отправь ID платного чата или перешли любое сообщение из него.",
        reply_markup=cancel_keyboard(),
    )
    await state.set_state(SetupStates.waiting_for_paid_chat)


@router.message(SetupStates.waiting_for_paid_chat)
async def process_paid_input(message: Message, state: FSMContext, bot: Bot) -> None:
    chat_id = _extract_chat_id(message)
    if not chat_id:
        await message.answer(
            "Не удалось определить ID чата.\n\n"
            "Отправь числовой ID (например <code>-1001234567890</code>) "
            "или перешли сообщение из чата.\n\n"
            "💡 Узнать ID: добавь @userinfobot в чат, он пришлёт ID.",
            parse_mode="HTML",
        )
        return

    account = await get_account_by_tg_id(message.from_user.id)
    if not account:
        account = await get_or_create_account(message.from_user.id)

    await update_account(account["id"], {"paid_chat_id": chat_id})
    await state.clear()

    # Проверяем права, но не блокируем сохранение
    is_admin, _ = await check_bot_admin(bot, chat_id)
    if not is_admin:
        await message.answer(
            f"✅ Платный чат привязан: <code>{chat_id}</code>\n\n"
            "⚠️ Бот пока не видит себя администратором — это нормально если только что добавил.\n"
            "Подожди 1-2 минуты и проверь /status.\n\n"
            "Убедись что бот добавлен как <b>администратор</b> с любыми правами.",
            parse_mode="HTML",
        )
        return

    await message.answer(
        f"✅ Платный чат привязан: <code>{chat_id}</code>\n\n"
        "Готово! Теперь создай первый источник:\n"
        "<code>/newsource YouTube_видео_1</code>",
        parse_mode="HTML",
    )


# ─── /swap ────────────────────────────────────────────────────────────────────

@router.message(Command("swap"))
async def cmd_swap(message: Message) -> None:
    account = await get_account_by_tg_id(message.from_user.id)
    if not account:
        await message.answer("Сначала запусти /start")
        return

    free_id = account.get("free_channel_id")
    paid_id = account.get("paid_chat_id")

    if not free_id or not paid_id:
        await message.answer("Нужно привязать оба чата перед тем как их менять.")
        return

    await update_account(account["id"], {"free_channel_id": paid_id, "paid_chat_id": free_id})
    await message.answer(
        f"🔄 Поменял местами:\n"
        f"📢 Канал: <code>{paid_id}</code>\n"
        f"🔐 Чат: <code>{free_id}</code>",
        parse_mode="HTML",
    )


# ─── /setprice ────────────────────────────────────────────────────────────────

@router.message(Command("setprice"))
async def cmd_setprice(message: Message) -> None:
    account = await get_account_by_tg_id(message.from_user.id)
    if not account:
        await message.answer("Сначала запусти /start")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("Использование: /setprice <i>сумма</i>\nПример: /setprice 2990", parse_mode="HTML")
        return

    try:
        price = float(args[1].replace(",", "."))
        if price <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Сумма должна быть положительным числом.")
        return

    await update_account(account["id"], {"product_price": str(price)})
    await message.answer(f"✅ Цена продукта установлена: <b>{price:.0f} ₽</b>", parse_mode="HTML")

    from src.services.sheets_sync import sync_to_sheets
    await sync_to_sheets(account["id"])


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _extract_chat_id(message: Message) -> int | None:
    # Пересланное сообщение из канала/группы (новый Bot API)
    if message.forward_origin:
        origin = message.forward_origin
        if hasattr(origin, "chat") and origin.chat:
            return origin.chat.id

    # Старый API
    if message.forward_from_chat:
        return message.forward_from_chat.id

    # Прямой ввод ID
    if message.text:
        text = message.text.strip()
        if text.lstrip("-").isdigit():
            return int(text)

    return None
