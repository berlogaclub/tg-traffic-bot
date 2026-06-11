"""
Тесты логики атрибуции и расчёта метрик (без реального DB).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─── Тесты защиты от деления на ноль ─────────────────────────────────────────

from src.services.analytics import _safe_div, fmt, SourceMetrics


def test_safe_div_normal():
    assert _safe_div(100.0, 4.0) == 25.0


def test_safe_div_zero_denominator():
    assert _safe_div(100.0, 0) is None


def test_safe_div_zero_numerator():
    assert _safe_div(0, 10.0) == 0.0


def test_fmt_none_returns_dash():
    assert fmt(None) == "—"


def test_fmt_zero():
    assert fmt(0.0) == "0"


def test_fmt_with_suffix():
    assert fmt(1234.0, 0, "₽") == "1,234₽"


def test_fmt_decimals():
    result = fmt(3.14159, 2)
    assert "3.14" in result


# ─── Тесты SourceMetrics ──────────────────────────────────────────────────────

def test_source_metrics_fields():
    m = SourceMetrics(
        name="test",
        source_id="abc",
        subscribers=100,
        customers=10,
        cost=50000.0,
        revenue=29900.0,
        conversion=10.0,
        cpf=500.0,
        cac=5000.0,
        romi=-40.2,
        payback=0.598,
    )
    assert m.name == "test"
    assert m.subscribers == 100
    assert m.customers == 10


# ─── Тесты first-touch атрибуции (unit, mock DB) ─────────────────────────────

@pytest.mark.asyncio
async def test_attribute_subscriber_first_touch():
    """Первый join — записывает source_id."""
    mock_source = {"id": "source-uuid-1"}
    mock_subscriber = {
        "account_id": "acc-1",
        "tg_user_id": 123,
        "source_id": "source-uuid-1",
        "attribution_locked": True,
    }

    with (
        patch("src.services.attribution.find_source_by_invite_name", new=AsyncMock(return_value=mock_source)),
        patch("src.services.attribution.run_sync", new=AsyncMock(return_value=mock_subscriber)),
        patch("src.services.attribution._insert_event", new=AsyncMock()),
    ):
        from src.services.attribution import attribute_subscriber
        result = await attribute_subscriber(
            account_id="acc-1",
            tg_user_id=123,
            invite_name="YouTube_video_1",
            username="testuser",
            full_name="Test User",
        )
        assert result["source_id"] == "source-uuid-1"
        assert result["attribution_locked"] is True


@pytest.mark.asyncio
async def test_attribute_subscriber_no_invite_link():
    """Вступление без invite_link → source_id=NULL."""
    mock_subscriber = {
        "account_id": "acc-1",
        "tg_user_id": 456,
        "source_id": None,
        "attribution_locked": True,
    }

    with (
        patch("src.services.attribution.run_sync", new=AsyncMock(return_value=mock_subscriber)),
        patch("src.services.attribution._insert_event", new=AsyncMock()),
    ):
        from src.services.attribution import attribute_subscriber
        result = await attribute_subscriber(
            account_id="acc-1",
            tg_user_id=456,
            invite_name=None,
            username=None,
            full_name=None,
        )
        assert result["source_id"] is None


@pytest.mark.asyncio
async def test_attribute_customer_with_subscriber():
    """Клиент со subscriber → наследует source_id."""
    mock_subscriber = {"id": "sub-uuid-1", "source_id": "source-uuid-1"}
    mock_customer = {
        "account_id": "acc-1",
        "tg_user_id": 123,
        "source_id": "source-uuid-1",
        "entry_type": "paid",
    }

    with (
        patch("src.services.attribution.run_sync", new=AsyncMock(side_effect=[mock_subscriber, mock_customer])),
        patch("src.services.attribution._insert_event", new=AsyncMock()),
    ):
        from src.services.attribution import attribute_customer
        result = await attribute_customer(
            account_id="acc-1",
            tg_user_id=123,
            product_price=2990.0,
        )
        assert result["source_id"] == "source-uuid-1"
        assert result["entry_type"] == "paid"


@pytest.mark.asyncio
async def test_attribute_customer_without_subscriber():
    """Клиент без subscriber → entry_type='manual', source_id=NULL."""
    mock_customer = {
        "account_id": "acc-1",
        "tg_user_id": 999,
        "source_id": None,
        "entry_type": "manual",
    }

    with (
        patch("src.services.attribution.run_sync", new=AsyncMock(side_effect=[None, mock_customer])),
        patch("src.services.attribution._insert_event", new=AsyncMock()),
    ):
        from src.services.attribution import attribute_customer
        result = await attribute_customer(
            account_id="acc-1",
            tg_user_id=999,
            product_price=2990.0,
        )
        assert result["source_id"] is None
        assert result["entry_type"] == "manual"


# ─── Тесты метрик ─────────────────────────────────────────────────────────────

def test_romi_calculation():
    revenue = 143520.0
    cost = 60000.0
    romi = _safe_div((revenue - cost) * 100, cost)
    assert romi is not None
    assert abs(romi - 139.2) < 0.1


def test_cac_calculation():
    cost = 60000.0
    customers = 48
    cac = _safe_div(cost, customers)
    assert cac is not None
    assert abs(cac - 1250.0) < 0.1


def test_metrics_zero_cost():
    cost = 0.0
    customers = 10
    romi = _safe_div((10 * 2990 - cost) * 100, cost)
    cac = _safe_div(cost, customers)
    assert romi is None
    assert cac == 0.0
