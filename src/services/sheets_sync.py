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


def _build_rows(account_id: str, db) -> tuple[list, list[str]]:
    """
    Строит список строк для записи в таблицу.
    Возвращает (rows_to_write, warnings).
    Бросает исключение при любой критической ошибке — не глотает их молча.
    """
    warnings: list[str] = []

    _acc = db.table("accounts").select("product_price").eq("id", account_id).limit(1).execute()
    if not _acc or not _acc.data:
        raise RuntimeError(f"account_id={account_id!r} не найден в таблице accounts")
    product_price = float(_acc.data[0].get("product_price") or 0)

    sources_res = db.table("sources").select("id, name").eq("account_id", account_id).execute()
    sources = sources_res.data if (sources_res and sources_res.data) else []
    logger.info("Синк: найдено источников=%d account=%s", len(sources), account_id)

    def sdiv(a, b):
        if not b:
            return ""
        return round(a / b, 2)

    rows: list = []
    for src in sources:
        src_id = src["id"]
        src_name = src["name"]

        subs_r = db.table("subscribers").select("id").eq("account_id", account_id).eq("source_id", src_id).execute()
        sub_count = len(subs_r.data) if (subs_r and subs_r.data) else 0

        custs_r = db.table("customers").select("id, amount").eq("account_id", account_id).eq("source_id", src_id).eq("entry_type", "paid").execute()
        custs = custs_r.data if (custs_r and custs_r.data) else []
        cust_count = len(custs)

        costs_r = db.table("costs").select("amount").eq("account_id", account_id).eq("source_id", src_id).execute()
        costs = costs_r.data if (costs_r and costs_r.data) else []
        total_cost = sum(float(c["amount"]) for c in costs if c.get("amount") is not None)

        revenue = sum(
            float(c["amount"]) if c.get("amount") is not None else product_price
            for c in custs
        )

        conv = fmt(sdiv(cust_count * 100, sub_count) if sub_count else None, 1)
        cpf = fmt(sdiv(total_cost, sub_count), 2)
        cac = fmt(sdiv(total_cost, cust_count), 2)
        romi = fmt(sdiv((revenue - total_cost) * 100, total_cost), 1)
        payback = fmt(sdiv(revenue, total_cost), 2)

        rows.append([
            src_name,
            sub_count,
            cust_count,
            conv,
            fmt(total_cost, 2),
            fmt(product_price, 2),
            fmt(revenue, 2),
            cpf,
            cac,
            romi,
            payback,
        ])
        logger.info("Синк: %r sub=%d cust=%d cost=%.2f rev=%.2f", src_name, sub_count, cust_count, total_cost, revenue)

    # "Не определён"
    unk_s = db.table("subscribers").select("id").eq("account_id", account_id).is_("source_id", "null").execute()
    unk_c = db.table("customers").select("id").eq("account_id", account_id).is_("source_id", "null").eq("entry_type", "paid").execute()
    unk_sub = len(unk_s.data) if (unk_s and unk_s.data) else 0
    unk_cust = len(unk_c.data) if (unk_c and unk_c.data) else 0
    rows.append(["Не определён", unk_sub, unk_cust, "—", "", "—", "—", "—", "—", "—", "—"])

    return rows, warnings


def _sync_blocking(account_id: str) -> str:
    """
    Синхронизирует данные в Google Sheets.
    Возвращает строку с кратким итогом (для отображения пользователю).
    Бросает исключение при критической ошибке.
    """
    gc = _get_gc()
    if not gc:
        raise RuntimeError("Google credentials не настроены (GOOGLE_CREDENTIALS_JSON)")

    if not config.google_sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID не задан в переменных окружения")

    db = get_db()

    spreadsheet = _retry_gspread(lambda: gc.open_by_key(config.google_sheet_id))
    ws = _ensure_sheet(spreadsheet)

    # Читаем текущие значения колонок ввода (Расход, Цена) — не затираем их
    all_values = _retry_gspread(lambda: ws.get_all_values())
    existing_inputs: dict[str, tuple[str, str]] = {}
    for row in all_values[1:]:
        if not row or not row[0]:
            continue
        name = row[0]
        cost_val = row[COL_COST] if len(row) > COL_COST else ""
        price_val = row[COL_PRICE] if len(row) > COL_PRICE else ""
        existing_inputs[name] = (cost_val, price_val)

    # Импорт расходов из листа в БД (если заполнены колонки ввода)
    for source_name, (cost_str, price_str) in existing_inputs.items():
        if source_name in ("Не определён",):
            continue
        _src = db.table("sources").select("id").eq("account_id", account_id).eq("name", source_name).limit(1).execute()
        source = _src.data[0] if (_src and _src.data) else None
        if not source:
            continue
        if cost_str:
            try:
                cost_amount = float(cost_str.replace(",", ".").replace("\u00a0", "").replace(" ", ""))
                if cost_amount > 0:
                    ex = db.table("costs").select("id").eq("account_id", account_id).eq("source_id", source["id"]).execute()
                    if not (ex and ex.data):
                        db.table("costs").insert({
                            "account_id": account_id,
                            "source_id": source["id"],
                            "amount": str(cost_amount),
                            "note": "Импорт из Google Sheets",
                        }).execute()
            except (ValueError, TypeError):
                pass
        if price_str:
            try:
                price = float(price_str.replace(",", ".").replace("\u00a0", "").replace(" ", ""))
                if price > 0:
                    db.table("accounts").update({"product_price": str(price)}).eq("id", account_id).execute()
            except (ValueError, TypeError):
                pass

    # Строим строки и пишем в таблицу
    rows, warnings = _build_rows(account_id, db)

    # Восстанавливаем колонки ввода которые были в таблице
    for row in rows:
        name = row[0]
        if name in existing_inputs:
            saved_cost, saved_price = existing_inputs[name]
            if saved_cost:
                row[COL_COST] = saved_cost
            if saved_price:
                row[COL_PRICE] = saved_price

    # Очищаем и пишем заново
    last_col = chr(ord("A") + len(HEADERS) - 1)
    clear_range = f"A2:{last_col}200"
    _retry_gspread(lambda: ws.batch_clear([clear_range]))

    end_row = 1 + len(rows)
    write_range = f"A2:{last_col}{end_row}"
    _retry_gspread(lambda: ws.update(write_range, rows))

    db.table("settings").update({"last_synced_at": "now()"}).eq("account_id", account_id).execute()

    src_count = len(rows) - 1  # без "Не определён"
    logger.info("Синк завершён account=%s src=%d", account_id, src_count)
    return f"Записано источников: {src_count}, строк: {len(rows)}"


async def sync_to_sheets(account_id: str) -> tuple[bool, str]:
    """
    Возвращает (success, message).
    success=True + итоговое сообщение при успехе.
    success=False + текст ошибки при провале.
    """
    try:
        result = await run_sync(lambda: _sync_blocking(account_id))
        return True, result or "Готово"
    except Exception as e:
        logger.error("Ошибка синка Google Sheets: %s", e, exc_info=True)
        return False, str(e)


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
