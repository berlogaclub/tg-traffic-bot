"""
Обработчик событий chat_member.
Ловит вступления в бесплатный канал и платный чат.
"""
import logging

from aiogram import Bot, F, Router
from aiogram.filters import ChatMemberUpdatedFilter, JOIN_TRANSITION, LEAVE_TRANSITION
from aiogram.types import CallbackQuery, ChatMemberUpdated, InlineKeyboardButton, InlineKeyboardMarkup

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
            # Фоновый синк без ожидания
            import asyncio
            from src.services.sheets_sync import sync_to_sheets
            asyncio.create_task(sync_to_sheets(account["id"]))
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

            # Не учитываем продажи без источника (source_id = NULL)
            if not source_id:
                logger.info(
                    "ℹ️ Клиент user=%s без источника — не уведомляем и не синкаем",
                    tg_user_id,
                )
                return

            logger.info(
                "✅ Клиент записан: user=%s entry_type=%s source=%s чат=%s",
                tg_user_id, entry_type, source_id, chat_id,
            )
            import asyncio
            from src.services.sheets_sync import sync_to_sheets
            asyncio.create_task(sync_to_sheets(account["id"]))

            # Уведомление только для заданного владельца (ID: 8612204954)
            NOTIFY_OWNER_ID = 8612204954
            try:
                from src.core.database import get_db
                from src.services.attribution import _one

                def _get_customer_and_source():
                    db = get_db()
                    # id клиента нужен для кнопки
                    cust_r = db.table("customers").select("id").eq("account_id", account["id"]).eq("tg_user_id", tg_user_id).limit(1).execute()
                    cust_id = _one(cust_r).get("id") if _one(cust_r) else None
                    # имя источника
                    result = db.table("sources").select("name").eq("id", source_id).limit(1).execute()
                    row = _one(result)
                    return cust_id, row.get("name") if row else "?"

                import asyncio
                cust_id, src_name = await asyncio.to_thread(_get_customer_and_source)

                msg = (
                    f"💰 <b>Новая продажа!</b>\n"
                    f"Источник: <b>{src_name}</b>\n"
                    f"Сумма: <b>{product_price:.0f} ₽</b>"
                )
                kb = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text="🚫 УБРАТЬ ИЗ УЧЁТА",
                        callback_data=f"sale_exclude:{cust_id}:{account['id']}",
                    )
                ]]) if cust_id else None

                await bot.send_message(NOTIFY_OWNER_ID, msg, parse_mode="HTML", reply_markup=kb)
            except Exception as e:
                logger.warning("Не удалось отправить уведомление: %s", e)
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


# ─── Callback: исключить / включить продажу ───────────────────────────────────

def _sale_keyboard(cust_id: str, account_id: str, excluded: bool) -> InlineKeyboardMarkup:
    if excluded:
        btn = InlineKeyboardButton(
            text="✅ УЧИТЫВАТЬ",
            callback_data=f"sale_include:{cust_id}:{account_id}",
        )
    else:
        btn = InlineKeyboardButton(
            text="🚫 УБРАТЬ ИЗ УЧЁТА",
            callback_data=f"sale_exclude:{cust_id}:{account_id}",
        )
    return InlineKeyboardMarkup(inline_keyboard=[[btn]])


@router.callback_query(F.data.startswith("sale_exclude:"))
async def cb_sale_exclude(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    cust_id, account_id = parts[1], parts[2]
    await _toggle_excluded(callback, bot, cust_id, account_id, exclude=True)


@router.callback_query(F.data.startswith("sale_include:"))
async def cb_sale_include(callback: CallbackQuery, bot: Bot) -> None:
    parts = callback.data.split(":")
    cust_id, account_id = parts[1], parts[2]
    await _toggle_excluded(callback, bot, cust_id, account_id, exclude=False)


async def _toggle_excluded(
    callback: CallbackQuery, bot: Bot, cust_id: str, account_id: str, exclude: bool
) -> None:
    from src.core.database import get_db, run_sync
    from src.services.sheets_sync import sync_to_sheets

    def _update():
        db = get_db()
        db.table("customers").update({"excluded": exclude}).eq("id", cust_id).execute()

    try:
        await run_sync(_update)
    except Exception as e:
        logger.error("Ошибка toggle_excluded cust=%s: %s", cust_id, e)
        await callback.answer("Ошибка обновления", show_alert=True)
        return

    # Обновляем кнопку на сообщении
    new_kb = _sale_keyboard(cust_id, account_id, excluded=exclude)
    status_line = "\n<i>🚫 Убрана из учёта</i>" if exclude else ""
    try:
        # Переписываем только клавиатуру, текст оставляем + добавляем статус
        original = callback.message.html_text or callback.message.text or ""
        # Убираем предыдущую статус-строку если она была
        clean = original.replace("\n<i>🚫 Убрана из учёта</i>", "").replace("\n🚫 Убрана из учёта", "")
        await callback.message.edit_text(
            clean + status_line,
            parse_mode="HTML",
            reply_markup=new_kb,
        )
    except Exception:
        # Если текст не изменился — редактируем только клавиатуру
        try:
            await callback.message.edit_reply_markup(reply_markup=new_kb)
        except Exception:
            pass

    # Обновляем таблицу в фоне
    import asyncio
    asyncio.create_task(sync_to_sheets(account_id))

    label = "Убрана из учёта" if exclude else "Снова учитывается"
    await callback.answer(label)
