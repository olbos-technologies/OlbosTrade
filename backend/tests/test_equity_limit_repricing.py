"""
_execute_signal's equity branch reprices the limit off a LIVE quote just
before submission, instead of trusting trade_plan.entry_price (the daily-bar
close computed at scan time, up to 15 min stale by the time the order
reaches the broker). Confirmed in production: DAY LIMIT orders sitting at
that stale price never filled, sat for the full 30-minute grace window, and
were cancelled as "order_unfilled_timeout" — repeatedly, on the same ticker,
every scan cycle.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.utils.market_hours  # noqa: F401 - ensures the dotted path below resolves


@pytest.fixture(autouse=True)
def _market_always_open():
    with patch("app.utils.market_hours.is_market_open", return_value=True):
        yield


def _clean_portfolio():
    from app.services.guardrails import PortfolioState
    return PortfolioState(
        current_value=100_000.0, starting_capital=100_000.0,
        daily_pnl=0.0, weekly_pnl=0.0, monthly_pnl=0.0,
        consecutive_losses=0, trades_today=0,
    )


def _equity_signal(action="BUY", ticker="AAPL", stale_entry_price=150.0):
    return {
        "id": "sig-reprice", "ticker": ticker, "action": action, "asset_type": "equity",
        "confidence": 0.95,
        "trade_plan": {
            "shares": 10, "entry_price": stale_entry_price,
            "stop_price": None, "target_price": None, "risk_reward": 2.0,
        },
        "indicators": {"volume_ratio": 1.5},
        "signal_score": 0.8, "iv_rank": 30.0, "regime": "normal",
    }


def _quote(bid, ask):
    q = MagicMock()
    q.bid_price = bid
    q.ask_price = ask
    return q


async def _run(signal, broker):
    from app.api.routes.trade_desk import _execute_signal

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=MagicMock(first=lambda: None))

    recorder = MagicMock()
    recorder.record_fill = AsyncMock(return_value="trade-uuid")

    with patch("app.api.routes.trade_desk._fetch_portfolio_state", return_value=_clean_portfolio()), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.services.trade_recorder.trade_recorder", recorder), \
         patch("app.core.database.AsyncSessionLocal", return_value=mock_session):
        return await _execute_signal(signal, approved_by="autopilot")


@pytest.mark.asyncio
async def test_buy_reprices_to_live_ask_with_marketable_buffer(stub_position_claim):
    """A stale entry_price of $150 with a live ask of $160 must submit near
    $160 (marketable), not the stale $150 that would never fill."""
    broker = MagicMock()
    broker.get_latest_quote = AsyncMock(return_value=_quote(bid=159.90, ask=160.00))
    broker.get_account_summary = AsyncMock(return_value=MagicMock(net_liquidation=100_000.0))
    broker.place_equity_order = AsyncMock(return_value=MagicMock(order_id="o1", status="submitted"))

    await _run(_equity_signal(action="BUY", stale_entry_price=150.0), broker)

    limit_price = broker.place_equity_order.call_args.kwargs["limit_price"]
    assert 160.00 <= limit_price < 160.20, (
        f"expected a small marketable buffer above the live ask (160.00), got {limit_price}"
    )


@pytest.mark.asyncio
async def test_sell_reprices_to_live_bid_with_marketable_buffer(stub_position_claim):
    broker = MagicMock()
    broker.get_latest_quote = AsyncMock(return_value=_quote(bid=140.00, ask=140.10))
    broker.get_account_summary = AsyncMock(return_value=MagicMock(net_liquidation=100_000.0))
    broker.place_equity_order = AsyncMock(return_value=MagicMock(order_id="o2", status="submitted"))

    await _run(_equity_signal(action="SELL", stale_entry_price=150.0), broker)

    limit_price = broker.place_equity_order.call_args.kwargs["limit_price"]
    assert 139.80 < limit_price <= 140.00, (
        f"expected a small marketable buffer below the live bid (140.00), got {limit_price}"
    )


@pytest.mark.asyncio
async def test_falls_back_to_stale_entry_price_when_quote_unavailable(stub_position_claim):
    """If the live quote fetch fails for any reason, the order must still go
    out at the original scan-time entry_price rather than blocking the trade
    entirely — a repricing improvement should never become a new failure mode."""
    broker = MagicMock()
    broker.get_latest_quote = AsyncMock(side_effect=RuntimeError("no market data subscription"))
    broker.get_account_summary = AsyncMock(return_value=MagicMock(net_liquidation=100_000.0))
    broker.place_equity_order = AsyncMock(return_value=MagicMock(order_id="o3", status="submitted"))

    await _run(_equity_signal(action="BUY", stale_entry_price=150.0), broker)

    limit_price = broker.place_equity_order.call_args.kwargs["limit_price"]
    assert limit_price == 150.0


@pytest.mark.asyncio
async def test_zero_ask_does_not_reprice_buy(stub_position_claim):
    """A zeroed/missing ask (e.g. a bad tick) must not produce a $0 limit
    order — falls back to the stale entry_price instead."""
    broker = MagicMock()
    broker.get_latest_quote = AsyncMock(return_value=_quote(bid=0, ask=0))
    broker.get_account_summary = AsyncMock(return_value=MagicMock(net_liquidation=100_000.0))
    broker.place_equity_order = AsyncMock(return_value=MagicMock(order_id="o4", status="submitted"))

    await _run(_equity_signal(action="BUY", stale_entry_price=150.0), broker)

    limit_price = broker.place_equity_order.call_args.kwargs["limit_price"]
    assert limit_price == 150.0
