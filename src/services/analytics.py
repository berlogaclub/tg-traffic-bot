"""
Расчёт метрик по источникам.
Все деления защищены: знаменатель 0 → None (отображается как «—»).
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

from src.core.database import get_db, run_sync

logger = logging.getLogger(__name__)


@dataclass
class SourceMetrics:
    name: str
    source_id: Optional[str]
    subscribers: int
    customers: int
    cost: float
    revenue: float
    conversion: Optional[float]  # %
    cpf: Optional[float]         # стоимость подписчика
    cac: Optional[float]         # стоимость клиента
    romi: Optional[float]        # %
    payback: Optional[float]


def _safe_div(a: float, b: float) -> Optional[float]:
    if b == 0:
        return None
    return a / b


def _compute_source_metrics(
    source_id: Optional[str],
    source_name: str,
    account_id: str,
    product_price: float,
) -> SourceMetrics:
    db = get_db()

    if source_id:
        subs_result = (
            db.table("subscribers")
            .select("id", count="exact")
            .eq("account_id", account_id)
            .eq("source_id", source_id)
            .execute()
        )
        custs_result = (
            db.table("customers")
            .select("id, amount", count="exact")
            .eq("account_id", account_id)
            .eq("source_id", source_id)
            .eq("entry_type", "paid")
            .eq("excluded", False)
            .execute()
        )
        costs_result = (
            db.table("costs")
            .select("amount")
            .eq("account_id", account_id)
            .eq("source_id", source_id)
            .execute()
        )
    else:
        subs_result = (
            db.table("subscribers")
            .select("id", count="exact")
            .eq("account_id", account_id)
            .is_("source_id", "null")
            .execute()
        )
        custs_result = (
            db.table("customers")
            .select("id, amount", count="exact")
            .eq("account_id", account_id)
            .is_("source_id", "null")
            .eq("entry_type", "paid")
            .eq("excluded", False)
            .execute()
        )
        costs_result = None

    sub_count = subs_result.count or 0
    cust_count = custs_result.count or 0

    custs_data = custs_result.data or []
    revenue = sum(
        float(c["amount"]) if c.get("amount") else product_price
        for c in custs_data
    )

    total_cost = 0.0
    if costs_result:
        total_cost = sum(float(c["amount"]) for c in (costs_result.data or []))

    conversion = _safe_div(cust_count * 100.0, sub_count)
    cpf = _safe_div(total_cost, sub_count)
    cac = _safe_div(total_cost, cust_count)
    romi = _safe_div((revenue - total_cost) * 100.0, total_cost)
    payback = _safe_div(revenue, total_cost)

    return SourceMetrics(
        name=source_name,
        source_id=source_id,
        subscribers=sub_count,
        customers=cust_count,
        cost=total_cost,
        revenue=revenue,
        conversion=conversion,
        cpf=cpf,
        cac=cac,
        romi=romi,
        payback=payback,
    )


async def compute_metrics(account_id: str) -> list[SourceMetrics]:
    def _query():
        db = get_db()
        _acc_res = (
            db.table("accounts")
            .select("product_price")
            .eq("id", account_id)
            .limit(1)
            .execute()
        )
        account = (_acc_res.data[0] if _acc_res and _acc_res.data else None)
        product_price = float(account.get("product_price") or 0) if account else 0.0

        sources = (
            db.table("sources")
            .select("id, name")
            .eq("account_id", account_id)
            .order("created_at")
            .execute()
            .data
            or []
        )

        metrics = []
        for src in sources:
            m = _compute_source_metrics(
                source_id=src["id"],
                source_name=src["name"],
                account_id=account_id,
                product_price=product_price,
            )
            metrics.append(m)

        unknown = _compute_source_metrics(
            source_id=None,
            source_name="Не определён",
            account_id=account_id,
            product_price=product_price,
        )
        metrics.append(unknown)

        metrics.sort(key=lambda m: m.revenue, reverse=True)
        return metrics

    return await run_sync(_query)


def fmt(value, decimals: int = 0, suffix: str = "") -> str:
    """Форматирует число. Принимает float, int, str (Supabase numeric) или None."""
    if value is None or value == "":
        return "—"
    try:
        v = float(value)
    except (ValueError, TypeError):
        return "—"
    if decimals == 0:
        return f"{v:,.0f}{suffix}"
    return f"{v:,.{decimals}f}{suffix}"


def format_stats_table(metrics: list[SourceMetrics]) -> str:
    if not metrics:
        return "Нет данных."

    lines = ["<pre>"]
    lines.append(
        f"{'Источник':<20} {'Подп':>5} {'Клиент':>7} {'Конв%':>6} "
        f"{'Расход':>8} {'CAC':>7} {'ROMI%':>7}"
    )
    lines.append("─" * 64)

    for m in metrics:
        lines.append(
            f"{m.name[:20]:<20} {m.subscribers:>5} {m.customers:>7} "
            f"{fmt(m.conversion, 1):>6} "
            f"{fmt(m.cost, 0, '₽'):>8} "
            f"{fmt(m.cac, 0, '₽'):>7} "
            f"{fmt(m.romi, 0, '%'):>7}"
        )

    lines.append("</pre>")
    return "\n".join(lines)


def format_source_detail(m: SourceMetrics) -> str:
    lines = [f"<b>📊 {m.name}</b>\n"]
    lines.append(f"👥 Подписчики:  <b>{m.subscribers}</b>")
    lines.append(f"💰 Клиенты:     <b>{m.customers}</b>")
    lines.append(f"📈 Конверсия:   <b>{fmt(m.conversion, 2)}%</b>")
    lines.append(f"💸 Расход:      <b>{fmt(m.cost, 2)} ₽</b>")
    lines.append(f"💵 Выручка:     <b>{fmt(m.revenue, 2)} ₽</b>")
    lines.append(f"🎯 CPF:         <b>{fmt(m.cpf, 2)} ₽</b>")
    lines.append(f"🎯 CAC:         <b>{fmt(m.cac, 2)} ₽</b>")
    lines.append(f"📊 ROMI:        <b>{fmt(m.romi, 1)}%</b>")
    lines.append(f"💹 Окупаемость: <b>{fmt(m.payback, 2)}x</b>")
    return "\n".join(lines)
