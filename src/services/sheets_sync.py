"""
Двусторонняя синхронизация с Google Sheets.
Сначала читаем колонки ввода (Расход, Цена продукта),
потом записываем рассчитанные метрики.
Колонки ввода НЕ затираем.
"""
import logging
import time
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from src.core.config import config
from src.core.database import get_db, run_sync
from src.services.analytics import compute_metrics, fmt

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_NAME = "Sources"

# Заголовки листа (порядок важен)
HEADERS = [
    "Источник",
    "Подписчики",
    "Клиенты",
    "Конв.%",
    "Расход (ввод)",
    "Цена продукта (ввод)",
    "Выручка",
    "CPF",
    "CAC",
    "ROMI%",
    "Окуп.",
]

# Индексы колонок ввода (0-based)
COL_COST = 4         # "Расход (ввод)"
COL_PRICE = 5        # "Цена продукта (ввод)"


def _get_gc() -> Optional[gspread.Client]:
    if not config.google_credentials:
        logger.warning("Google credentials не настроены, синк пропущен")
        return None
    creds = Credentials.from_service_account_info(config.google_credentials, scopes=SCOPES)
    return gspread.authorize(creds)


def _retry_gspread(func, retries: int = 3, delay: float = 5.0):
    """Простой retry с экспоненциальным backoff для 429/503."""
    for attempt in range(retries):
        try:
            return func()
        except gspread.exceptions.APIError as e:
            status = getattr(e.response, "status_code", None)
            if status in (429, 503) and attempt < retries - 1:
                wait = delay * (2 ** attempt)
                logger.warning("Sheets rate-limit/503, ждём %.0f сек (попытка %d)", wait, attempt + 1)
                time.sleep(wait)
            else:
                raise


def _ensure_sheet(spreadsheet: gspread.Spreadsheet) -> gspread.Worksheet:
    try:
        ws = spreadsheet.worksheet(SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=SHEET_NAME, rows=100, cols=len(HEADERS))
        logger.info("Создан новый лист '%s'", SHEET_NAME)

    existing = ws.row_values(1)
    if existing != HEADERS:
        ws.update("A1", [HEADERS])
    return ws


def _sync_blocking(account_id: str) -> None:
    gc = _get_gc()
    if not gc:
        return

    if not config.google_sheet_id:
        logger.warning("GOOGLE_SHEET_ID не задан, синк пропущен")
        return

    db = get_db()
    account = (
        db.table("accounts")
        .select("product_price")
        .eq("id", account_id)
        .maybe_single()
        .execute()
        .data
    )
    if not account:
        return

    spreadsheet = _retry_gspread(lambda: gc.open_by_key(config.google_sheet_id))
    ws = _ensure_sheet(spreadsheet)

    all_values = _retry_gspread(lambda: ws.get_all_values())
    # all_values[0] — заголовки, all_values[1:] — данные

    # Читаем текущие значения ввода (Расход и Цена)
    existing_inputs: dict[str, tuple[str, str]] = {}
    for row in all_values[1:]:
        if not row or not row[0]:
            continue
        name = row[0]
        cost_val = row[COL_COST] if len(row) > COL_COST else ""
        price_val = row[COL_PRICE] if len(row) > COL_PRICE else ""
        existing_inputs[name] = (cost_val, price_val)

    # Применяем расходы из таблицы в БД
    for source_name, (cost_str, price_str) in existing_inputs.items():
        if source_name == "Не определён":
            continue

        source = (
            db.table("sources")
            .select("id")
            .eq("account_id", account_id)
            .eq("name", source_name)
            .maybe_single()
            .execute()
            .data
        )
        if not source:
            continue

        if cost_str:
            try:
                cost_amount = float(cost_str.replace(",", ".").replace(" ", ""))
                if cost_amount > 0:
                    existing_cost = (
                        db.table("costs")
                        .select("id")
                        .eq("account_id", account_id)
                        .eq("source_id", source["id"])
                        .execute()
                        .data
                    )
                    if not existing_cost:
                        db.table("costs").insert(
                            {
                                "account_id": account_id,
                                "source_id": source["id"],
                                "amount": str(cost_amount),
                                "note": "Импорт из Google Sheets",
                            }
                        ).execute()
            except ValueError:
                pass

        if price_str:
            try:
                price = float(price_str.replace(",", ".").replace(" ", ""))
                if price > 0:
                    db.table("accounts").update({"product_price": str(price)}).eq("id", account_id).execute()
            except ValueError:
                pass

    # Пересчитываем метрики (после обновления расходов)
    account_fresh = (
        db.table("accounts").select("product_price").eq("id", account_id).maybe_single().execute().data
    )
    product_price = float(account_fresh.get("product_price") or 0) if account_fresh else 0.0

    sources = db.table("sources").select("id, name").eq("account_id", account_id).order("created_at").execute().data or []

    rows_to_write = []
    for src in sources:
        source_id = src["id"]
        subs = db.table("subscribers").select("id", count="exact").eq("account_id", account_id).eq("source_id", source_id).execute()
        custs = db.table("customers").select("id, amount", count="exact").eq("account_id", account_id).eq("source_id", source_id).eq("entry_type", "paid").execute()
        costs_data = db.table("costs").select("amount").eq("account_id", account_id).eq("source_id", source_id).execute().data or []

        sub_count = subs.count or 0
        cust_count = custs.count or 0
        total_cost = sum(float(c["amount"]) for c in costs_data)
        custs_list = custs.data or []
        revenue = sum(float(c["amount"]) if c.get("amount") else product_price for c in custs_list)

        def sdiv(a, b):
            return round(a / b, 2) if b else ""

        input_cost, input_price = existing_inputs.get(src["name"], ("", ""))

        rows_to_write.append([
            src["name"],
            sub_count,
            cust_count,
            fmt(sdiv(cust_count * 100, sub_count) if sub_count else None, 1),
            input_cost or fmt(total_cost, 2),
            input_price or fmt(product_price, 2),
            fmt(revenue, 2),
            fmt(sdiv(total_cost, sub_count), 2),
            fmt(sdiv(total_cost, cust_count), 2),
            fmt(sdiv((revenue - total_cost) * 100, total_cost), 1),
            fmt(sdiv(revenue, total_cost), 2),
        ])

    # "Не определён"
    unk_subs = db.table("subscribers").select("id", count="exact").eq("account_id", account_id).is_("source_id", "null").execute()
    unk_custs = db.table("customers").select("id, amount", count="exact").eq("account_id", account_id).is_("source_id", "null").eq("entry_type", "paid").execute()
    rows_to_write.append([
        "Не определён",
        unk_subs.count or 0,
        unk_custs.count or 0,
        "—", "", "—", "—", "—", "—", "—", "—",
    ])

    # Записываем данные начиная со строки 2
    if rows_to_write:
        end_row = 1 + len(rows_to_write)
        range_str = f"A2:{chr(ord('A') + len(HEADERS) - 1)}{end_row}"
        _retry_gspread(lambda: ws.update(range_str, rows_to_write))

    # Обновляем last_synced_at
    db.table("settings").update(
        {"last_synced_at": "now()"}
    ).eq("account_id", account_id).execute()

    logger.info("Sheets синк завершён для account=%s, строк=%d", account_id, len(rows_to_write))


async def sync_to_sheets(account_id: str) -> None:
    try:
        await run_sync(lambda: _sync_blocking(account_id))
    except Exception as e:
        logger.error("Ошибка синка Google Sheets: %s", e, exc_info=True)


async def setup_sheet_for_account(account_id: str) -> bool:
    """Создаёт структуру листа и сохраняет sheet_id в settings."""
    def _setup():
        gc = _get_gc()
        if not gc or not config.google_sheet_id:
            return False
        spreadsheet = _retry_gspread(lambda: gc.open_by_key(config.google_sheet_id))
        _ensure_sheet(spreadsheet)
        db = get_db()
        db.table("settings").update(
            {"sheet_id": config.google_sheet_id, "sync_enabled": True}
        ).eq("account_id", account_id).execute()
        return True

    try:
        return await run_sync(_setup)
    except Exception as e:
        logger.error("Ошибка настройки листа: %s", e)
        return False
