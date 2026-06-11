from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def sources_keyboard(sources: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for src in sources:
        sid = src["id"]
        name = src["name"]
        builder.row(
            InlineKeyboardButton(text=f"📋 {name}", callback_data=f"src_info:{sid}"),
        )
        builder.row(
            InlineKeyboardButton(text="📊 Статистика", callback_data=f"src_stats:{sid}"),
            InlineKeyboardButton(text="🗑 Удалить", callback_data=f"src_delete:{sid}"),
        )
    builder.row(InlineKeyboardButton(text="➕ Новый источник", callback_data="src_new"))
    return builder.as_markup()


def confirm_delete_keyboard(source_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅ Удалить", callback_data=f"src_delete_confirm:{source_id}"),
        InlineKeyboardButton(text="❌ Отмена", callback_data="src_delete_cancel"),
    )
    return builder.as_markup()


def costs_keyboard(costs: list[dict]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for cost in costs:
        cid = cost["id"]
        src_name = cost.get("sources", {}).get("name", "?") if cost.get("sources") else "?"
        amount = cost.get("amount", "?")
        builder.row(
            InlineKeyboardButton(
                text=f"{src_name}: {amount} ₽",
                callback_data=f"cost_info:{cid}",
            ),
            InlineKeyboardButton(text="🗑", callback_data=f"cost_delete:{cid}"),
        )
    return builder.as_markup()


def setup_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="📢 Привязать канал", callback_data="setup_channel"),
        InlineKeyboardButton(text="🔐 Привязать чат", callback_data="setup_paid"),
    )
    builder.row(InlineKeyboardButton(text="📊 Статус", callback_data="setup_status"))
    return builder.as_markup()


def cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel"))
    return builder.as_markup()
