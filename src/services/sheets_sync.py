"""
Двусторонняя синхронизация с Google Sheets.

Архитектура:
- Колонки D (Подписчики) и E (Клиенты) — числа из БД.
- Колонки G (Расход) и H (Цена продукта) — ручной ввод, сохраняются при синке.
- Колонки F, I–M — формулы Google Sheets, пересчитываются автоматически.
- Строка ИТОГО — формулы агрегации по всем источникам.
"""
import logging
import time
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from src.core.config import config
from src.core.database import get_db, run_sync

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_NAME = "Sources"

# Заголовки листа (порядок критичен — формулы завязаны на позиции колонок)
HEADERS = [
    "Источник",            # A=0  — имя источника
    "Ссылка",              # B=1  — invite-ссылка (автозаполнение)
    "Ссылка на площадку",  # C=2  — ручной ввод, сохраняется
    "Подписчики",          # D=3  — из БД
    "Клиенты",             # E=4  — из БД
    "Конв.%",              # F=5  — ФОРМУЛА
    "Расход",              # G=6  — ручной ввод
    "Цена продукта",       # H=7  — ручной ввод
    "Выручка",             # I=8  — ФОРМУЛА
    "CPF",                 # J=9  — ФОРМУЛА
    "CAC",                 # K=10 — ФОРМУЛА
    "ROMI%",               # L=11 — ФОРМУЛА
    "Окуп.",               # M=12 — ФОРМУЛА
]

LAST_COL = chr(ord("A") + len(HEADERS) - 1)  # "M"

# Индексы колонок ввода (0-based)
COL_AD_LINK = 2   # C — «Ссылка на площадку»
COL_COST    = 6   # G — «Расход»
COL_PRICE   = 7   # H — «Цена продукта»

# Цвета для форматирования (RGB 0.0–1.0)
COLOR_HEADER  = {"red": 0.16, "green": 0.32, "blue": 0.58}   # тёмно-синий
COLOR_TOTALS  = {"red": 1.0,  "green": 0.80, "blue": 0.2}    # янтарный
COLOR_UNKNOWN = {"red": 0.95, "green": 0.95, "blue": 0.95}   # светло-серый
COLOR_WHITE   = {"red": 1.0,  "green": 1.0,  "blue": 1.0}
COLOR_WTEXT   = {"red": 1.0,  "green": 1.0,  "blue": 1.0}    # белый текст
COLOR_DTEXT   = {"red": 0.2,  "green": 0.2,  "blue": 0.2}    # тёмный текст


def _get_gc() -> Optional[gspread.Client]:
    if not config.google_credentials:
        logger.warning("Google credentials не настроены, синк пропущен")
        return None
    creds = Credentials.from_service_account_info(config.google_credentials, scopes=SCOPES)
    return gspread.authorize(creds)


def _retry_gspread(func, retries: int = 3, delay: float = 5.0):
    """Retry с экспоненциальным backoff для 429/503."""
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
        ws = spreadsheet.add_worksheet(title=SHEET_NAME, rows=200, cols=len(HEADERS))
        logger.info("Создан новый лист '%s'", SHEET_NAME)
    # Всегда обновляем заголовки (на случай изменения структуры)
    _retry_gspread(lambda: ws.update("A1", [HEADERS], value_input_option="USER_ENTERED"))
    return ws


# ─────────────────────────── Формулы ──────────────────────────────────────────

def _row_formulas(r: int) -> dict:
    """Возвращает формулы для строки r (1-based). Используется Google Sheets синтаксис."""
    return {
        "conv":    f'=IFERROR(ROUND(E{r}/D{r}*100,1),"—")',
        "revenue": f'=IFERROR(ROUND(E{r}*H{r},2),"—")',
        "cpf":     f'=IFERROR(ROUND(G{r}/D{r},2),"—")',
        "cac":     f'=IFERROR(ROUND(G{r}/E{r},2),"—")',
        "romi":    f'=IFERROR(ROUND((I{r}-G{r})/G{r}*100,1),"—")',
        "payback": f'=IFERROR(ROUND(I{r}/G{r},2),"—")',
    }


def _totals_row(data_start: int, data_end: int) -> list:
    """
    Строка ИТОГО с агрегирующими формулами.
    data_start..data_end — диапазон строк источников (включительно).
    """
    def sr(col: str) -> str:
        return f"{col}{data_start}:{col}{data_end}"

    return [
        "ИТОГО", "", "",
        f"=SUM({sr('D')})",                                              # D Подписчики
        f"=SUM({sr('E')})",                                              # E Клиенты
        f'=IFERROR(ROUND(SUM({sr("E")})/SUM({sr("D")})*100,1),"—")',    # F Конв.%
        f"=SUM({sr('G')})",                                              # G Расход
        "",                                                               # H Цена (н/д для итого)
        f"=IFERROR(SUM({sr('I')}),0)",                                   # I Выручка
        f'=IFERROR(ROUND(SUM({sr("G")})/SUM({sr("D")}),2),"—")',        # J CPF
        f'=IFERROR(ROUND(SUM({sr("G")})/SUM({sr("E")}),2),"—")',        # K CAC
        f'=IFERROR(ROUND((SUM({sr("I")})-SUM({sr("G")}))/SUM({sr("G")})*100,1),"—")',  # L ROMI%
        f'=IFERROR(ROUND(SUM({sr("I")})/SUM({sr("G")}),2),"—")',        # M Окуп.
    ]


# ─────────────────────────── Форматирование ───────────────────────────────────

def _apply_formatting(ws: gspread.Worksheet, data_end: int, totals_row_num: int) -> None:
    """Применяет форматирование к заголовку, строке «Не определён» и ИТОГО."""

    def cell_fmt(bg: dict, fg: dict, bold: bool = False, align: str = "LEFT") -> dict:
        return {
            "backgroundColor": bg,
            "textFormat": {"bold": bold, "foregroundColor": fg},
            "horizontalAlignment": align,
        }

    try:
        # Заголовок (строка 1) — тёмно-синий, белый текст, жирный
        _retry_gspread(lambda: ws.format(
            f"A1:{LAST_COL}1",
            cell_fmt(COLOR_HEADER, COLOR_WTEXT, bold=True, align="CENTER"),
        ))
        # Строка "Не определён" — светло-серый фон
        _retry_gspread(lambda: ws.format(
            f"A{data_end}:{LAST_COL}{data_end}",
            cell_fmt(COLOR_UNKNOWN, COLOR_DTEXT),
        ))
        # Строка ИТОГО — янтарный фон, жирный
        _retry_gspread(lambda: ws.format(
            f"A{totals_row_num}:{LAST_COL}{totals_row_num}",
            cell_fmt(COLOR_TOTALS, COLOR_DTEXT, bold=True),
        ))
        # Заморозка первой строки
        spreadsheet = ws.spreadsheet
        spreadsheet.batch_update({
            "requests": [{
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": ws.id,
                        "gridProperties": {"frozenRowCount": 1},
                    },
                    "fields": "gridProperties.frozenRowCount",
                }
            }]
        })
    except Exception as e:
        logger.warning("Форматирование Sheets пропущено (не критично): %s", e)


# ─────────────────────────── Основной синк ────────────────────────────────────

def _fetch_db_data(account_id: str, db) -> tuple[dict, list[dict], int, int]:
    """Читает данные из БД. Возвращает (account, sources_with_stats, unk_sub, unk_cust)."""
    _acc = db.table("accounts").select("product_price").eq("id", account_id).limit(1).execute()
    if not _acc or not _acc.data:
        raise RuntimeError(f"account_id={account_id!r} не найден в таблице accounts")
    account = _acc.data[0]

    sources_res = db.table("sources").select("id, name, invite_link").eq("account_id", account_id).execute()
    raw_sources = sources_res.data if (sources_res and sources_res.data) else []

    sources = []
    for src in raw_sources:
        src_id = src["id"]

        subs_r = db.table("subscribers").select("id").eq("account_id", account_id).eq("source_id", src_id).execute()
        sub_count = len(subs_r.data) if (subs_r and subs_r.data) else 0

        custs_r = db.table("customers").select("id").eq("account_id", account_id).eq("source_id", src_id).eq("entry_type", "paid").execute()
        cust_count = len(custs_r.data) if (custs_r and custs_r.data) else 0

        costs_r = db.table("costs").select("amount").eq("account_id", account_id).eq("source_id", src_id).execute()
        total_cost = sum(float(c["amount"]) for c in (costs_r.data or []) if c.get("amount") not in (None, ""))

        sources.append({
            "name":        src["name"],
            "invite_link": src.get("invite_link") or "",
            "sub_count":   sub_count,
            "cust_count":  cust_count,
            "total_cost":  total_cost,
        })

    logger.info("Синк: найдено источников=%d account=%s", len(sources), account_id)
    return account, sources


def _sync_blocking(account_id: str) -> str:
    gc = _get_gc()
    if not gc:
        raise RuntimeError("Google credentials не настроены (GOOGLE_CREDENTIALS_JSON)")
    if not config.google_sheet_id:
        raise RuntimeError("GOOGLE_SHEET_ID не задан в переменных окружения")

    db = get_db()
    spreadsheet = _retry_gspread(lambda: gc.open_by_key(config.google_sheet_id))
    ws = _ensure_sheet(spreadsheet)

    # ── 1. Читаем пользовательский ввод из таблицы ────────────────────────────
    all_values = _retry_gspread(lambda: ws.get_all_values())
    # {name: {"ad_link", "cost", "price"}}
    existing_inputs: dict[str, dict] = {}
    for row in all_values[1:]:
        if not row or not row[0]:
            continue
        name = row[0]
        existing_inputs[name] = {
            "ad_link": row[COL_AD_LINK] if len(row) > COL_AD_LINK else "",
            "cost":    row[COL_COST]    if len(row) > COL_COST    else "",
            "price":   row[COL_PRICE]   if len(row) > COL_PRICE   else "",
        }

    # ── 2. Импортируем расходы/цену из таблицы в БД ──────────────────────────
    for source_name, vals in existing_inputs.items():
        if source_name in ("Не определён", "ИТОГО"):
            continue
        cost_str  = vals.get("cost", "")
        price_str = vals.get("price", "")
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

    # ── 3. Читаем актуальные данные из БД ────────────────────────────────────
    account, sources = _fetch_db_data(account_id, db)
    product_price = float(account.get("product_price") or 0)

    # ── 4. Строим строки (D, E — числа; F, I–M — формулы) ───────────────────
    DATA_START = 2  # первая строка данных (строка 1 — заголовок)
    rows: list[list] = []

    for i, src in enumerate(sources):
        r = DATA_START + i
        f = _row_formulas(r)
        saved = existing_inputs.get(src["name"], {})

        cost_val  = saved.get("cost") or (src["total_cost"] if src["total_cost"] > 0 else "")
        price_val = saved.get("price") or (product_price if product_price > 0 else "")

        rows.append([
            src["name"],            # A Источник
            src["invite_link"],     # B Ссылка
            saved.get("ad_link", ""),  # C Ссылка на площадку
            src["sub_count"],       # D Подписчики
            src["cust_count"],      # E Клиенты
            f["conv"],              # F Конв.% — ФОРМУЛА
            cost_val,               # G Расход
            price_val,              # H Цена продукта
            f["revenue"],           # I Выручка — ФОРМУЛА
            f["cpf"],               # J CPF — ФОРМУЛА
            f["cac"],               # K CAC — ФОРМУЛА
            f["romi"],              # L ROMI% — ФОРМУЛА
            f["payback"],           # M Окуп. — ФОРМУЛА
        ])

    # Строка "Не определён" — убрана по требованию: неатрибутированный трафик не отображается
    data_end = DATA_START + len(rows) - 1 if rows else DATA_START - 1

    # Строка ИТОГО (через пустую строку)
    totals_row_num = data_end + 2
    totals = _totals_row(DATA_START, data_end)

    # ── 5. Очищаем лист и пишем данные + ИТОГО ───────────────────────────────
    clear_range = f"A2:{LAST_COL}300"
    _retry_gspread(lambda: ws.batch_clear([clear_range]))

    if rows:
        # Данные источников
        write_range = f"A{DATA_START}:{LAST_COL}{data_end}"
        _retry_gspread(lambda: ws.update(
            write_range, rows, value_input_option="USER_ENTERED"
        ))

        # Строка ИТОГО
        totals_range = f"A{totals_row_num}:{LAST_COL}{totals_row_num}"
        _retry_gspread(lambda: ws.update(
            totals_range, [totals], value_input_option="USER_ENTERED"
        ))

    # ── 6. Форматирование ────────────────────────────────────────────────────
    _apply_formatting(ws, data_end, totals_row_num)

    db.table("settings").update({"last_synced_at": "now()"}).eq("account_id", account_id).execute()

    src_count = len(sources)
    logger.info("Синк завершён account=%s src=%d итого_строка=%d", account_id, src_count, totals_row_num)
    return f"Записано источников: {src_count}, строк данных: {len(rows)}, ИТОГО — строка {totals_row_num}"


async def sync_to_sheets(account_id: str) -> tuple[bool, str]:
    """Возвращает (success, message)."""
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
