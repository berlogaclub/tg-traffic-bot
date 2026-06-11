"""
Проверка состояния бота: права, привязка каналов, синк.
"""
import logging
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from src.services.attribution import get_settings

logger = logging.getLogger(__name__)


async def check_bot_admin(bot: Bot, chat_id: int) -> tuple[bool, bool]:
    """
    Возвращает (is_admin, can_invite_users).
    """
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(chat_id=chat_id, user_id=me.id)
        is_admin = member.status in ("administrator", "creator")
        can_invite = getattr(member, "can_invite_users", False)
        return is_admin, can_invite
    except TelegramAPIError as e:
        logger.warning("Ошибка проверки прав в chat_id=%s: %s", chat_id, e)
        return False, False


async def get_status_text(bot: Bot, account: dict) -> str:
    lines = ["<b>📊 Статус бота</b>\n"]

    free_id = account.get("free_channel_id")
    paid_id = account.get("paid_chat_id")
    price = account.get("product_price") or 0

    # Бесплатный канал
    if free_id:
        try:
            chat = await bot.get_chat(free_id)
            is_admin, can_invite = await check_bot_admin(bot, free_id)
            status = "✅" if is_admin else "⚠️ нет прав"
            invite_status = "✅" if can_invite else "⚠️ нет can_invite_users"
            lines.append(f"📢 Канал: <b>{chat.title}</b>")
            lines.append(f"   Статус: {status}, Ссылки: {invite_status}")
        except TelegramAPIError:
            lines.append(f"📢 Канал: <code>{free_id}</code> (не удалось получить)")
    else:
        lines.append("📢 Канал: <b>не привязан</b> → /setchannel")

    # Платный чат
    if paid_id:
        try:
            chat = await bot.get_chat(paid_id)
            is_admin, _ = await check_bot_admin(bot, paid_id)
            status = "✅" if is_admin else "⚠️ нет прав"
            lines.append(f"🔐 Платный чат: <b>{chat.title}</b>")
            lines.append(f"   Статус: {status}")
        except TelegramAPIError:
            lines.append(f"🔐 Платный чат: <code>{paid_id}</code> (не удалось получить)")
    else:
        lines.append("🔐 Платный чат: <b>не привязан</b> → /setpaid")

    lines.append(f"\n💰 Цена продукта: <b>{price} ₽</b>")

    if account.get("id"):
        settings = await get_settings(account["id"])
        if settings:
            sync = "✅ включён" if settings.get("sync_enabled") else "❌ выключен"
            last = settings.get("last_synced_at") or "никогда"
            lines.append(f"📊 Google Sheets: {sync}")
            lines.append(f"   Последний синк: {last}")

    return "\n".join(lines)
