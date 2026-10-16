"""
Open-order visibility: an empty order book must never read as "no risk".

Context (2026-08-28): the desk held $220,714 of equity notional across three
positions and nothing in the system could say whether any of them had a
protective stop. Equity entries are submitted as brackets with a GTC stop
child, but positions the reconciler adopts from the broker never had a bracket,
and `trades` has no stop column — so the question was unanswerable from inside
the app. This route answers it, and the hard requirement is that it cannot
answer it *wrongly* in the reassuring direction.

Run with: pytest tests/test_open_orders_route.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes.portfolio import portfolio_open_orders

from conftest import single_operator_conn

# Batch E2 gave these routes a `conn` so they can resolve a trade scope; with
# auth disabled it is never read. See conftest.single_operator_conn.
_CONN = single_operator_conn()



def _order(symbol="MRVL", order_type="STP", remaining=599.0, **kw):
    o = {
        "order_id": 1, "parent_id": None, "symbol": symbol, "sec_type": "STK",
        "action": "SELL", "order_type": order_type, "quantity": remaining,
        "limit_price": None, "stop_price": 214.0, "tif": "GTC",
        "status": "PreSubmitted", "filled": 0.0, "remaining": remaining,
        "is_protective": str(order_type).upper().startswith("STP"),
    }
    o.update(kw)
    return o


def _trade(ticker="MRVL", qty=599, strategy="equity"):
    return SimpleNamespace(underlying=ticker, quantity=qty,
                           spread_type="equity_long", strategy=strategy)


def _session(trades):
    s = AsyncMock()
    s.__aenter__ = AsyncMock(return_value=s)
    s.__aexit__ = AsyncMock(return_value=False)
    res = MagicMock()
    res.scalars.return_value = MagicMock(all=lambda: trades)
    s.execute = AsyncMock(return_value=res)
    return s


async def _call(order_result, trades):
    broker = MagicMock()
    broker.get_open_orders = AsyncMock(return_value=order_result)
    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=_session(trades)), \
         patch("app.broker.ibkr_coordinator.ibkr_coordinator.submit",
               new=AsyncMock(return_value=order_result)):
        return await portfolio_open_orders(_CONN)


# ── the reassuring-wrong-answer guard ────────────────────────────────────────

@pytest.mark.asyncio
async def test_empty_cache_is_not_reported_as_reliable():
    """The whole point. An empty list from a cache fall-back means "we don't
    know", not "no stops exist" — and the caller must be able to tell."""
    out = await _call({"source": "cache_after_refresh_failed", "orders": []},
                      [_trade("MRVL")])

    assert out["unprotected_is_reliable"] is False
    # It still reports the position as unprotected — but flagged as unreliable,
    # so a UI cannot render a confident all-clear off it.
    assert [p["ticker"] for p in out["positions_without_stop"]] == ["MRVL"]


@pytest.mark.asyncio
async def test_refreshed_empty_book_is_reliable():
    out = await _call({"source": "refreshed", "orders": []}, [_trade("MRVL")])
    assert out["unprotected_is_reliable"] is True
    assert out["positions_without_stop"][0]["ticker"] == "MRVL"


# ── protection detection ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_resting_stop_marks_the_position_protected():
    out = await _call({"source": "refreshed", "orders": [_order("MRVL")]},
                      [_trade("MRVL")])
    assert out["positions_without_stop"] == []
    assert out["protected_tickers"] == ["MRVL"]
    assert out["protective_order_count"] == 1


@pytest.mark.asyncio
async def test_a_limit_order_does_not_count_as_protection():
    """A resting take-profit is not a stop. Counting any open order as
    protection would be the same reassuring-wrong-answer failure."""
    out = await _call(
        {"source": "refreshed", "orders": [_order("MRVL", order_type="LMT")]},
        [_trade("MRVL")])
    assert [p["ticker"] for p in out["positions_without_stop"]] == ["MRVL"]
    assert out["protective_order_count"] == 0


@pytest.mark.asyncio
async def test_a_fully_filled_stop_no_longer_protects():
    """remaining == 0 means it already triggered; it is not standing guard."""
    out = await _call(
        {"source": "refreshed", "orders": [_order("MRVL", remaining=0.0, filled=599.0)]},
        [_trade("MRVL")])
    assert [p["ticker"] for p in out["positions_without_stop"]] == ["MRVL"]


@pytest.mark.asyncio
async def test_stp_lmt_counts_as_protective():
    out = await _call(
        {"source": "refreshed", "orders": [_order("MRVL", order_type="STP LMT")]},
        [_trade("MRVL")])
    assert out["positions_without_stop"] == []


# ── the real production shape ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_adopted_positions_are_flagged_as_such():
    """LITE and MSTR were adopted from the broker and never had a bracket
    submitted. Saying so distinguishes 'never protected' from 'lost its
    stop', which are different problems."""
    out = await _call(
        {"source": "refreshed", "orders": [_order("MRVL")]},
        [_trade("MRVL", 599, "equity"),
         _trade("LITE", 68, "adopted_untracked"),
         _trade("MSTR", -156, "adopted_untracked")])

    gaps = {p["ticker"]: p["adopted_from_broker"] for p in out["positions_without_stop"]}
    assert gaps == {"LITE": True, "MSTR": True}
    assert out["open_position_count"] == 3
    assert out["protected_tickers"] == ["MRVL"]


@pytest.mark.asyncio
async def test_symbol_matching_is_case_insensitive():
    out = await _call({"source": "refreshed", "orders": [_order("mrvl")]},
                      [_trade("MRVL")])
    assert out["positions_without_stop"] == []


# ── degradation ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_broker_failure_reports_unavailable_rather_than_empty():
    broker = MagicMock()
    broker.get_open_orders = AsyncMock(side_effect=ConnectionError("not connected"))
    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.broker.ibkr_coordinator.ibkr_coordinator.submit",
               new=AsyncMock(side_effect=ConnectionError("not connected"))):
        out = await portfolio_open_orders(_CONN)

    assert out["available"] is False
    assert "not connected" in out["reason"]
    # Critically: no positions_without_stop key to misread as "all clear".
    assert "positions_without_stop" not in out


@pytest.mark.asyncio
async def test_broker_without_an_order_book_says_so():
    broker = MagicMock(spec=[])   # no get_open_orders attribute
    with patch("app.broker.broker_factory.get_broker", return_value=broker):
        out = await portfolio_open_orders(_CONN)
    assert out["available"] is False
    assert "order book" in out["reason"]
