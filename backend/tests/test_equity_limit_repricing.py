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
@pytest.mark.parametrize("reason", ["kill_switch"])
async def test_entry_halt_keeps_its_reason_and_releases_unsent_claim(stub_position_claim, reason):
    from app.services.execution_interlock import EntryHalted
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[])  # the portfolio gate reads it
    broker.get_latest_quote = AsyncMock(return_value=_quote(150, 151))
    broker.get_account_summary = AsyncMock(return_value=MagicMock(net_liquidation=100_000.0))
    broker.place_equity_order = AsyncMock(side_effect=EntryHalted(reason))
    result = await _run(_equity_signal(), broker)
    assert result["result"] == "blocked"
    assert result["reason"] == reason
    stub_position_claim.resolve.assert_awaited_once()
    # Dispatch reserves the claim before calling the adapter; a proven unsent
    # veto releases that reservation rather than retaining an unknown outcome.
    stub_position_claim.mark_unknown.assert_not_awaited()


@pytest.mark.asyncio
async def test_unconfirmed_cancel_keeps_claim_even_after_pending_row_written(stub_position_claim):
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[])  # the portfolio gate reads it
    broker.get_latest_quote = AsyncMock(return_value=_quote(150, 151))
    broker.get_account_summary = AsyncMock(return_value=MagicMock(net_liquidation=100_000.0))
    broker.place_equity_order = AsyncMock(return_value=MagicMock(order_id="o-pending", status="pending"))
    result = await _run(_equity_signal(), broker)
    assert result["order_status"] == "pending"
    stub_position_claim.mark_unknown.assert_awaited_once()
    stub_position_claim.resolve.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("reset_before_return", [False, True])
async def test_halt_during_quote_blocks_dispatch(stub_position_claim, monkeypatch, reset_before_return):
    from app.services.kill_switch import KillSwitch
    switch = KillSwitch()
    monkeypatch.setattr("app.services.kill_switch.kill_switch_service", switch)
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[])  # the portfolio gate reads it
    broker.get_account_summary = AsyncMock(return_value=MagicMock(net_liquidation=100_000.0))
    broker.place_equity_order = AsyncMock()

    async def quote_then_halt(*args):
        switch._entry_epoch += 1
        switch._engaged = not reset_before_return
        return _quote(150, 151)

    broker.get_latest_quote = quote_then_halt
    result = await _run(_equity_signal(), broker)
    assert result["result"] == "blocked" and result["reason"] == "kill_switch"
    broker.place_equity_order.assert_not_awaited()
    stub_position_claim.resolve.assert_awaited_once()


@pytest.mark.asyncio
async def test_buy_reprices_to_live_ask_with_marketable_buffer(stub_position_claim):
    """A stale entry_price of $150 with a live ask of $160 must submit near
    $160 (marketable), not the stale $150 that would never fill."""
    broker = MagicMock()
    # Successful broker read is a prerequisite, not a swallowed mock error.
    broker.get_positions = AsyncMock(return_value=[])
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
    # Successful broker read is a prerequisite, not a swallowed mock error.
    broker.get_positions = AsyncMock(return_value=[])
    broker.get_latest_quote = AsyncMock(return_value=_quote(bid=140.00, ask=140.10))
    broker.get_account_summary = AsyncMock(return_value=MagicMock(net_liquidation=100_000.0))
    broker.place_equity_order = AsyncMock(return_value=MagicMock(order_id="o2", status="submitted"))

    await _run(_equity_signal(action="SELL", stale_entry_price=150.0), broker)

    limit_price = broker.place_equity_order.call_args.kwargs["limit_price"]
    assert 139.80 < limit_price <= 140.00, (
        f"expected a small marketable buffer below the live bid (140.00), got {limit_price}"
    )


def _broker_with_quote(quote_mock):
    broker = MagicMock()
    # Successful broker read is a prerequisite, not a swallowed mock error.
    broker.get_positions = AsyncMock(return_value=[])
    broker.get_latest_quote = quote_mock
    broker.get_account_summary = AsyncMock(return_value=MagicMock(net_liquidation=100_000.0))
    broker.place_equity_order = AsyncMock(return_value=MagicMock(order_id="o3", status="submitted"))
    return broker


@pytest.mark.asyncio
async def test_no_entry_when_the_live_quote_fails(stub_position_claim, caplog):
    """FAIL CLOSED. A failed quote used to fall back, at DEBUG, to the
    scan-time entry_price — so a dead data feed still opened positions priced
    off a daily close. Now the entry is skipped, said at WARNING, and the claim
    released because nothing was sent. Reverses the earlier
    test_falls_back_to_stale_entry_price_when_quote_unavailable on purpose."""
    import logging
    broker = _broker_with_quote(AsyncMock(side_effect=RuntimeError("no market data subscription")))

    with caplog.at_level(logging.WARNING):
        result = await _run(_equity_signal(action="BUY", stale_entry_price=150.0), broker)

    assert result["result"] == "skipped"
    assert result["reason"] == "live_quote_unavailable"
    broker.place_equity_order.assert_not_awaited()
    stub_position_claim.mark_submitted.assert_not_awaited()
    stub_position_claim.resolve.assert_awaited_once()
    assert any("no usable live quote" in r.getMessage() and r.levelno >= logging.WARNING
               for r in caplog.records), "the skip must be visible, not a DEBUG line"


@pytest.mark.asyncio
@pytest.mark.parametrize("action,bid,ask", [
    ("BUY", 150.0, 0),             # zeroed ask
    ("BUY", 150.0, -1.0),          # negative ask
    ("BUY", 150.0, float("nan")),  # NaN ask
    ("BUY", 150.0, float("inf")),  # inf ask
    ("BUY", 150.0, None),          # missing ask
    ("SELL", 0, 151.0),            # zeroed bid
    ("SELL", "n/a", 151.0),        # non-numeric bid
])
async def test_no_entry_on_an_unusable_quote_side(stub_position_claim, action, bid, ask):
    """Only the side the order prices off matters (ask for BUY, bid for SELL),
    and any unusable value on it skips the entry rather than substituting the
    stale price."""
    broker = _broker_with_quote(AsyncMock(return_value=_quote(bid=bid, ask=ask)))

    result = await _run(_equity_signal(action=action, stale_entry_price=150.0), broker)

    assert (result["result"], result["reason"]) == ("skipped", "live_quote_unavailable")
    broker.place_equity_order.assert_not_awaited()
    stub_position_claim.resolve.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_bad_bid_does_not_block_a_buy(stub_position_claim):
    """The other side of the book is irrelevant: a BUY with a good ask and a
    zero bid still goes out, repriced off the ask."""
    broker = _broker_with_quote(AsyncMock(return_value=_quote(bid=0, ask=160.0)))

    await _run(_equity_signal(action="BUY", stale_entry_price=150.0), broker)

    limit_price = broker.place_equity_order.call_args.kwargs["limit_price"]
    assert 160.0 < limit_price <= 160.0 * 1.002
