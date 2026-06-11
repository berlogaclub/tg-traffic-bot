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
    try:
        chat_id = event.chat.id
        tg_user_id = event.new_chat_member.user.id

        old_status = event.old_chat_member.status
        new_status = event.new_chat_member.status
        logger.info(
            "chat_member: chat=%s user=%s %s→%s invite=%s",
            chat_id, tg_user_id, old_status, new_status,
            event.invite_link.name if event.invite_link else None,
        )

        # Пропускаем самого бота
        me = await bot.get_me()
        if tg_user_id == me.id:
            return

        if not _is_join(event):
            return

        # Проверяем бесплатный канал
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
                "✅ Подписчик записан: user=%s источник=%r канал=%s",
                tg_user_id, invite_name, chat_id,
            )
            return

        # Проверяем платный чат
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

            logger.info(
                "✅ Клиент записан: user=%s entry_type=%s source=%s чат=%s",
                tg_user_id, entry_type, source_id, chat_id,
            )

            try:
                owner_id = account.get("tg_user_id")
                if owner_id:
                    import asyncio
                    from src.core.database import get_db
                    from src.services.attribution import _one

                    def _get_source_name():
                        if not source_id:
                            return None
                        db = get_db()
                        result = db.table("sources").select("name").eq("id", source_id).limit(1).execute()
                        row = _one(result)
                        return row.get("name") if row else "?"

                    src_name = await asyncio.to_thread(_get_source_name)
                    if src_name:
                        msg = f"💰 Новая продажа!\nИсточник: <b>{src_name}</b>\nСумма: <b>{product_price:.0f} ₽</b>"
                    else:
                        msg = f"💰 Новая продажа!\nИсточник: <b>не определён</b> ({entry_type})\nСумма: <b>{product_price:.0f} ₽</b>"
                    await bot.send_message(owner_id, msg, parse_mode="HTML")
            except Exception as e:
                logger.warning("Не удалось отправить уведомление владельцу: %s", e)
            return

        logger.debug("chat_member из неизвестного чата %s — игнорируем", chat_id)

    except Exception as e:
        logger.error("Ошибка в on_chat_member: %s", e, exc_info=True)


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
