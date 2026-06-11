"""
Модули атрибуции подписчиков и клиентов.
Принцип: FIRST-TOUCH, идемпотентный UPSERT.
"""
import logging
from typing import Optional

from src.core.database import get_db, run_sync

logger = logging.getLogger(__name__)


def _one(result) -> Optional[dict]:
    """Безопасно достаёт первую запись из результата Supabase."""
    if result is None:
        return None
    data = getattr(result, "data", None)
    if not data:
        return None
    return data[0] if isinstance(data, list) else data


async def get_or_create_account(tg_user_id: int) -> dict:
    def _query():
        db = get_db()
        result = (
            db.table("accounts")
            .upsert({"tg_user_id": tg_user_id}, on_conflict="tg_user_id")
            .execute()
        )
        return _one(result)

    account = await run_sync(_query)
    if account:
        def _create_settings():
            db = get_db()
            db.table("settings").upsert(
                {"account_id": account["id"]}, on_conflict="account_id"
            ).execute()

        await run_sync(_create_settings)
    return account


async def get_account_by_tg_id(tg_user_id: int) -> Optional[dict]:
    def _query():
        db = get_db()
        result = (
            db.table("accounts")
            .select("*")
            .eq("tg_user_id", tg_user_id)
            .limit(1)
            .execute()
        )
        return _one(result)

    return await run_sync(_query)


async def get_account_by_free_channel(channel_id: int) -> Optional[dict]:
    def _query():
        db = get_db()
        result = (
            db.table("accounts")
            .select("*")
            .eq("free_channel_id", channel_id)
            .limit(1)
            .execute()
        )
        return _one(result)

    return await run_sync(_query)


async def get_account_by_paid_chat(chat_id: int) -> Optional[dict]:
    def _query():
        db = get_db()
        result = (
            db.table("accounts")
            .select("*")
            .eq("paid_chat_id", chat_id)
            .limit(1)
            .execute()
        )
        return _one(result)

    return await run_sync(_query)


async def update_account(account_id: str, data: dict) -> None:
    def _query():
        get_db().table("accounts").update(data).eq("id", account_id).execute()

    await run_sync(_query)


async def find_source_by_invite_name(account_id: str, invite_name: str) -> Optional[dict]:
    def _query():
        db = get_db()
        result = (
            db.table("sources")
            .select("*")
            .eq("account_id", account_id)
            .eq("invite_name", invite_name)
            .limit(1)
            .execute()
        )
        return _one(result)

    return await run_sync(_query)


async def attribute_subscriber(
    account_id: str,
    tg_user_id: int,
    invite_name: Optional[str],
    username: Optional[str],
    full_name: Optional[str],
    raw_event: Optional[dict] = None,
) -> dict:
    """
    Записывает подписчика с first-touch атрибуцией.
    Повторный join — атрибуция не меняется (attribution_locked=true).
    """
    source_id = None
    if invite_name:
        source = await find_source_by_invite_name(account_id, invite_name)
        if source:
            source_id = source["id"]
        else:
            logger.warning(
                "invite_name=%r не найден в sources для account=%s, source_id=NULL",
                invite_name,
                account_id,
            )

    def _upsert():
        db = get_db()
        row = {
            "account_id": account_id,
            "tg_user_id": tg_user_id,
            "source_id": source_id,
            "username": username,
            "full_name": full_name,
            "attribution_locked": True,
        }
        result = (
            db.table("subscribers")
            .upsert(row, on_conflict="account_id,tg_user_id", ignore_duplicates=False)
            .execute()
        )
        return _one(result) or row

    subscriber = await run_sync(_upsert)

    await _insert_event(
        account_id=account_id,
        tg_user_id=tg_user_id,
        chat_kind="free",
        event_type="join",
        invite_name=invite_name,
        raw=raw_event,
    )

    return subscriber


async def attribute_customer(
    account_id: str,
    tg_user_id: int,
    product_price: float,
    raw_event: Optional[dict] = None,
) -> dict:
    """
    Записывает клиента, наследуя source_id от subscriber.
    Если subscriber не найден — entry_type='manual', source_id=NULL.
    """

    def _find_subscriber():
        db = get_db()
        result = (
            db.table("subscribers")
            .select("id, source_id")
            .eq("account_id", account_id)
            .eq("tg_user_id", tg_user_id)
            .limit(1)
            .execute()
        )
        return _one(result)

    subscriber = await run_sync(_find_subscriber)

    if subscriber:
        source_id = subscriber.get("source_id")
        subscriber_id = subscriber["id"]
        entry_type = "paid"
    else:
        source_id = None
        subscriber_id = None
        entry_type = "manual"

    amount = product_price if product_price and product_price > 0 else None

    def _upsert():
        db = get_db()
        row = {
            "account_id": account_id,
            "tg_user_id": tg_user_id,
            "source_id": source_id,
            "subscriber_id": subscriber_id,
            "entry_type": entry_type,
            "amount": str(amount) if amount else None,
        }
        result = (
            db.table("customers")
            .upsert(row, on_conflict="account_id,tg_user_id", ignore_duplicates=True)
            .execute()
        )
        return _one(result) or row

    customer = await run_sync(_upsert)

    await _insert_event(
        account_id=account_id,
        tg_user_id=tg_user_id,
        chat_kind="paid",
        event_type="join",
        invite_name=None,
        raw=raw_event,
    )

    return customer


async def _insert_event(
    account_id: str,
    tg_user_id: int,
    chat_kind: str,
    event_type: str,
    invite_name: Optional[str],
    raw: Optional[dict],
) -> None:
    def _insert():
        get_db().table("events").insert(
            {
                "account_id": account_id,
                "tg_user_id": tg_user_id,
                "chat_kind": chat_kind,
                "event_type": event_type,
                "invite_name": invite_name,
                "raw": raw,
            }
        ).execute()

    await run_sync(_insert)


async def create_source(account_id: str, name: str, invite_link: str, invite_name: str) -> dict:
    def _insert():
        db = get_db()
        result = db.table("sources").insert(
            {
                "account_id": account_id,
                "name": name,
                "invite_link": invite_link,
                "invite_name": invite_name,
            }
        ).execute()
        if not result or not result.data:
            raise RuntimeError(
                f"Supabase вернул пустой результат при вставке источника. "
                f"result={result}"
            )
        return result.data[0]

    return await run_sync(_insert)


async def get_sources(account_id: str) -> list[dict]:
    def _query():
        db = get_db()
        result = (
            db.table("sources")
            .select("*")
            .eq("account_id", account_id)
            .order("created_at")
            .execute()
        )
        return result.data if result else []

    return await run_sync(_query)


async def get_source_by_name(account_id: str, name: str) -> Optional[dict]:
    def _query():
        db = get_db()
        result = (
            db.table("sources")
            .select("*")
            .eq("account_id", account_id)
            .eq("name", name)
            .limit(1)
            .execute()
        )
        return _one(result)

    return await run_sync(_query)


async def delete_source(source_id: str) -> None:
    def _delete():
        get_db().table("sources").delete().eq("id", source_id).execute()

    await run_sync(_delete)


async def add_cost(
    account_id: str,
    source_id: str,
    amount: float,
    note: Optional[str] = None,
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
) -> dict:
    def _insert():
        db = get_db()
        result = (
            db.table("costs")
            .insert(
                {
                    "account_id": account_id,
                    "source_id": source_id,
                    "amount": str(amount),
                    "note": note,
                    "period_start": period_start,
                    "period_end": period_end,
                }
            )
            .execute()
        )
        return _one(result) or {}

    return await run_sync(_insert)


async def get_costs(account_id: str) -> list[dict]:
    def _query():
        db = get_db()
        result = (
            db.table("costs")
            .select("*, sources(name)")
            .eq("account_id", account_id)
            .order("created_at", desc=True)
            .execute()
        )
        return result.data if result else []

    return await run_sync(_query)


async def delete_cost(cost_id: str) -> None:
    def _delete():
        get_db().table("costs").delete().eq("id", cost_id).execute()

    await run_sync(_delete)


async def get_settings(account_id: str) -> Optional[dict]:
    def _query():
        db = get_db()
        result = (
            db.table("settings")
            .select("*")
            .eq("account_id", account_id)
            .limit(1)
            .execute()
        )
        return _one(result)

    return await run_sync(_query)


async def update_settings(account_id: str, data: dict) -> None:
    def _query():
        get_db().table("settings").update(data).eq("account_id", account_id).execute()

    await run_sync(_query)
