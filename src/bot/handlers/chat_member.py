"""
Обработчик событий chat_member.
Ловит вступления в бесплатный канал и платный чат.
"""
import logging

from aiogram import Bot, Router
from aiogram.filters import ChatMemberUpdatedFilter, JOIN_TRANSITION, LEAVE_TRANSITION
from aiogram.types import ChatMemberUpdated

from src.services.attribution import (
    attribute_customer,
    attribute_subscriber,
    get_account_by_free_channel,
    get_account_by_paid_chat,
)

logger = logging.getLogger(__name__)
router = Router()


def _is_join(event: ChatMemberUpdated) -> bool:
    old = event.old_chat_member.status
    new = event.new_chat_member.status
    return old in ("left", "kicked") and new in ("member", "restricted", "administrator")


def _is_leave(event: ChatMemberUpdated) -> bool:
    old = event.old_chat_member.status
    new = event.new_chat_member.status
    return old in ("member", "restricted", "administrator") and new in ("left", "kicked")


@router.chat_member()
async def on_chat_member(event: ChatMemberUpdated, bot: Bot) -> None:
    chat_id = event.chat.id
    tg_user_id = event.new_chat_member.user.id

    # Пропускаем самого бота
    me = await bot.get_me()
    if tg_user_id == me.id:
        return

    if _is_join(event):
        account = await get_account_by_free_channel(chat_id)
        if account:
            invite_name = None
            if event.invite_link:
                invite_name = event.invite_link.name

            user = event.new_chat_member.user
            username = user.username
            full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or None

            await attribute_subscriber(
                account_id=account["id"],
                tg_user_id=tg_user_id,
                invite_name=invite_name,
                username=username,
                full_name=full_name,
                raw_event={"chat_id": chat_id, "invite_name": invite_name},
            )
            logger.info(
                "Подписчик user=%s источник=%r канал=%s",
                tg_user_id, invite_name, chat_id,
            )
            return

        account = await get_account_by_paid_chat(chat_id)
        if account:
            product_price = float(account.get("product_price") or 0)
            customer = await attribute_customer(
                account_id=account["id"],
                tg_user_id=tg_user_id,
                product_price=product_price,
                raw_event={"chat_id": chat_id},
            )
            entry_type = customer.get("entry_type", "paid")
            source_id = customer.get("source_id")

            try:
                owner_id = account.get("tg_user_id")
                if owner_id:
                    if source_id:
                        from src.core.database import get_db
                        def _get_source_name():
                            db = get_db()
                            result = db.table("sources").select("name").eq("id", source_id).maybe_single().execute()
                            return result.data.get("name") if result.data else "?"
                        import asyncio
                        src_name = await asyncio.to_thread(_get_source_name)
                        msg = f"💰 Новая продажа!\nИсточник: <b>{src_name}</b>\nСумма: <b>{product_price:.0f} ₽</b>"
                    else:
                        msg = f"💰 Новая продажа!\nИсточник: <b>не определён</b> ({entry_type})\nСумма: <b>{product_price:.0f} ₽</b>"
                    await bot.send_message(owner_id, msg, parse_mode="HTML")
            except Exception as e:
                logger.warning("Не удалось отправить уведомление владельцу: %s", e)

            logger.info(
                "Клиент user=%s entry_type=%s source=%s чат=%s",
                tg_user_id, entry_type, source_id, chat_id,
            )


@router.my_chat_member()
async def on_my_chat_member(event: ChatMemberUpdated, bot: Bot) -> None:
    """Отслеживаем изменение статуса самого бота в чатах."""
    new_status = event.new_chat_member.status
    chat_id = event.chat.id

    if new_status in ("left", "kicked"):
        logger.warning("Бот удалён из чата %s", chat_id)
        account = await get_account_by_free_channel(chat_id)
        if not account:
            account = await get_account_by_paid_chat(chat_id)
        if account:
            try:
                await bot.send_message(
                    account["tg_user_id"],
                    f"⚠️ Бот удалён из чата <code>{chat_id}</code>! "
                    "Аналитика по этому чату остановлена.",
                    parse_mode="HTML",
                )
            except Exception:
                pass

    elif new_status == "administrator":
        logger.info("Бот стал администратором в чате %s", chat_id)
