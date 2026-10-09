"""Coverage for trade_desk route handlers, portfolio-state read, dispatcher,
and the options execution branch."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.api.routes.trade_desk as td
from app.api.routes.trade_desk import (
    ClosePositionRequest, ManualTradeRequest, KillSwitchRequest, SetExecutionModeRequest,
    RiskGateError, _execute_signal, _fetch_portfolio_state, approve_signal,
    close_position, get_execution_log, get_execution_mode, get_kill_switch,
    get_pending, handle_signal, manual_trade, reject_all_pending, reject_signal,
    set_execution_mode, set_kill_switch,
)
from app.services.execution_mode import ExecutionMode
from app.services.guardrails import PortfolioState


@pytest.fixture(autouse=True)
def _market_open():
    with patch("app.utils.market_hours.is_market_open", return_value=True):
        yield


@pytest.fixture(autouse=True)
def _account_guard_ok():
    # The account-mode guard is exercised in test_account_guard.py; here it passes
    # by default so execution-path tests reach the broker submission stage.
    with patch("app.services.account_guard.verify_account_mode",
               new=AsyncMock(return_value=(True, "account DU-test (paper)"))):
        yield


def _clean():
    return PortfolioState(current_value=100_000.0, starting_capital=100_000.0,
                          daily_pnl=0.0, weekly_pnl=0.0, monthly_pnl=0.0,
                          consecutive_losses=0, trades_today=0)


# ── _fetch_portfolio_state ───────────────────────────────────────────────────────
def _pf_session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    result = MagicMock()
    result.scalar = MagicMock(return_value=0)
    result.scalars.return_value = MagicMock(all=lambda: [])
    session.execute = AsyncMock(return_value=result)
    return session


def _conn(user: dict | None = None):
    """Minimal stand-in for the HTTPConnection FastAPI injects into the
    approval routes. `state.user` is what `current_user` reads, so an empty
    dict models auth being disabled — the case where there is no authenticated
    actor to record."""
    from types import SimpleNamespace
    return SimpleNamespace(state=SimpleNamespace(user=user or {}))


@pytest.mark.asyncio
async def test_fetch_portfolio_state_success():
    broker = MagicMock()
    broker.get_account_summary = AsyncMock(return_value=MagicMock(net_liquidation=123456.0))
    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=_pf_session()):
        st = await _fetch_portfolio_state()
    assert st.current_value == 123456.0
    assert st.trades_today == 0 and st.consecutive_losses == 0


@pytest.mark.asyncio
async def test_fetch_portfolio_state_counts_consecutive_losses():
    session = _pf_session()
    session.execute = AsyncMock(return_value=MagicMock(
        scalar=MagicMock(return_value=2),
        scalars=lambda: MagicMock(all=lambda: [-5.0, -3.0, 10.0, -1.0])))
    with patch("app.broker.broker_factory.get_broker", side_effect=Exception("no broker")), \
         patch("app.core.database.AsyncSessionLocal", return_value=session):
        st = await _fetch_portfolio_state()
    assert st.consecutive_losses == 2     # stops at the first non-loss
    from app.core.config import settings as _cfg
    assert st.current_value == _cfg.starting_capital   # broker failed → config fallback


@pytest.mark.asyncio
async def test_fetch_portfolio_state_fail_closed():
    with patch("app.broker.broker_factory.get_broker", side_effect=Exception("x")), \
         patch("app.core.database.AsyncSessionLocal", side_effect=Exception("db down")):
        with pytest.raises(RiskGateError):
            await _fetch_portfolio_state()


# ── kill switch + execution mode endpoints ────────────────────────────────────────
@pytest.mark.asyncio
async def test_kill_switch_get_set():
    td._kill_switch.clear()
    assert (await get_kill_switch())["engaged"] is False
    # engage() must return a REPORT, not a bare AsyncMock. The route reads its
    # counts, statuses and errors now (PR #64) — a mock whose return_value is a
    # MagicMock made `result.get("errors")` yield a MagicMock, which is not
    # iterable. The old route discarded the result entirely, so this passed
    # while asserting nothing about the payload.
    _report = {"positions_flattened": 0, "orders_cancelled": 0,
               "flatten_statuses": {}, "errors": []}
    with patch.object(td.kill_switch_service, "engage",
                      new=AsyncMock(return_value=_report)), \
         patch.object(td.kill_switch_service, "reset",
                      new=AsyncMock(return_value={"reset": True})):
        out = await set_kill_switch(KillSwitchRequest(engaged=True))
        assert out["engaged"] is True
        await set_kill_switch(KillSwitchRequest(engaged=False))
    td._kill_switch.clear()


@pytest.mark.asyncio
async def test_execution_mode_get_set_and_invalid():
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    txn = MagicMock()
    txn.__aenter__ = AsyncMock(return_value=txn)
    txn.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=txn)
    session.add = MagicMock()

    out = await get_execution_mode()
    assert "mode" in out
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        ok = await set_execution_mode(SetExecutionModeRequest(mode="copilot"))
        assert ok["mode"] == "copilot"
        with pytest.raises(Exception):
            await set_execution_mode(SetExecutionModeRequest(mode="bogus"))
        await set_execution_mode(SetExecutionModeRequest(mode="manual"))


# ── pending / approve / reject ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_pending_lists_queue():
    with patch.object(td, "_get_pending_approvals",
                      new=AsyncMock(return_value=[{"id": "s1", "ticker": "SPY", "queued_at": "2026-01-01"}])):
        out = await get_pending()
    assert out["count"] == 1 and out["pending"][0]["ticker"] == "SPY"


@pytest.mark.asyncio
async def test_approve_signal_executes():
    with patch.object(td, "_resolve_pending_approval",
                      new=AsyncMock(return_value={"id": "s1", "ticker": "SPY", "asset_type": "equity"})), \
         patch.object(td, "_execute_signal",
                      new=AsyncMock(return_value={"result": "submitted", "ticker": "SPY"})), \
         patch.object(td, "_log_execution", new=AsyncMock()) as log_mock:
        out = await approve_signal("s1", _conn({"id": "u-1"}))
    assert out["result"] == "submitted"
    log_mock.assert_awaited_once()
    assert log_mock.await_args.args[0]["approved_by"] == "user"


@pytest.mark.asyncio
async def test_approve_records_the_authenticated_actor():
    """"approved_by" is a role; "approved_by_actor" is an identity.

    An audit trail that says "user" for every decision cannot answer which
    person authorised a given order, which is the question it exists for.
    """
    with patch.object(td, "_resolve_pending_approval",
                      new=AsyncMock(return_value={"id": "s1", "ticker": "SPY"})) as claim, \
         patch.object(td, "_execute_signal",
                      new=AsyncMock(return_value={"result": "submitted"})), \
         patch.object(td, "_log_execution", new=AsyncMock()) as log_mock:
        await approve_signal("s1", _conn({"id": "u-7", "email": "a@b.c"}))

    assert claim.await_args.kwargs["actor"] == "u-7"
    assert log_mock.await_args.args[0]["approved_by_actor"] == "u-7"


@pytest.mark.asyncio
async def test_approve_does_not_invent_an_actor_when_auth_is_off():
    with patch.object(td, "_resolve_pending_approval",
                      new=AsyncMock(return_value={"id": "s1", "ticker": "SPY"})) as claim, \
         patch.object(td, "_execute_signal",
                      new=AsyncMock(return_value={"result": "submitted"})), \
         patch.object(td, "_log_execution", new=AsyncMock()):
        await approve_signal("s1", _conn())

    assert claim.await_args.kwargs["actor"] is None


@pytest.mark.asyncio
async def test_approve_signal_not_found():
    with patch.object(td, "_resolve_pending_approval", new=AsyncMock(return_value=None)):
        with pytest.raises(Exception):
            await approve_signal("missing", _conn())


@pytest.mark.asyncio
async def test_reject_signal():
    with patch.object(td, "_resolve_pending_approval",
                      new=AsyncMock(return_value={"id": "s2", "ticker": "QQQ", "action": "BUY"})), \
         patch.object(td, "_log_execution", new=AsyncMock()) as log_mock:
        out = await reject_signal("s2", _conn({"id": "u-1"}))
    assert out["result"] == "rejected" and out["rejected_by"] == "user"
    log_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_reject_signal_not_found():
    with patch.object(td, "_resolve_pending_approval", new=AsyncMock(return_value=None)):
        with pytest.raises(Exception):
            await reject_signal("nope", _conn())


@pytest.mark.asyncio
async def test_reject_all_pending_bulk_rejects():
    """reject_all_pending() should mark every pending row rejected and log each one."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from types import SimpleNamespace

    # Build two fake pending ExecutionEvent rows.
    def _make_row(signal_id, ticker, action):
        row = MagicMock()
        row.signal_id = signal_id
        row.payload = {"ticker": ticker, "action": action, "asset_type": "equity"}
        row.status = "pending"
        return row

    rows = [_make_row("s1", "SPY", "BUY"), _make_row("s2", "QQQ", "SELL")]

    # Patch the DB session so we never touch a real DB.
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=mock_session)
    mock_session.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=rows))))
    )

    # AsyncSessionLocal is imported locally inside reject_all_pending so we
    # patch it at the source module rather than on the trade_desk module.
    with patch("app.core.database.AsyncSessionLocal", return_value=mock_session), \
         patch.object(td, "_log_execution", new=AsyncMock()) as log_mock:
        result = await reject_all_pending()

    assert result["rejected"] == 2
    assert log_mock.await_count == 2
    # Every row should have its status flipped.
    for row in rows:
        assert row.status == "rejected"
    # Log entries must carry the right rejection marker.
    logged_tickers = {call.args[0]["ticker"] for call in log_mock.await_args_list}
    assert logged_tickers == {"SPY", "QQQ"}
    for call in log_mock.await_args_list:
        assert call.args[0]["rejected_by"] == "user:bulk"


# ── manual trade + execution log ──────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_manual_trade_success_logs():
    with patch.object(td, "_execute_signal",
                      new=AsyncMock(return_value={"result": "submitted", "ticker": "AAPL"})), \
         patch.object(td, "_log_execution", new=AsyncMock()) as log_mock:
        out = await manual_trade(ManualTradeRequest(ticker="aapl", action="buy", shares=5))
    assert out["result"] == "submitted"
    log_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_manual_trade_error_raises():
    with patch.object(td, "_execute_signal",
                      new=AsyncMock(return_value={"result": "error", "error": "boom"})):
        with pytest.raises(Exception):
            await manual_trade(ManualTradeRequest(ticker="aapl", action="buy", shares=5))


# ── close_position (manual close, separate from _execute_signal) ──────────────

def _fake_trade_session(trade):
    result = MagicMock(scalar_one_or_none=MagicMock(return_value=trade))
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=result)
    return session


def _open_trade(spread_type="equity_long", status="open", quantity=10):
    t = MagicMock()
    t.id = "11111111-1111-1111-1111-111111111111"
    t.status = status
    t.spread_type = spread_type
    t.underlying = "AAPL"
    t.quantity = quantity
    return t


def _open_options_trade(spread_type="put", quantity=2):
    t = _open_trade(spread_type=spread_type, quantity=quantity)
    t.strategy = "bull_put_spread"
    t.short_strike = Decimal("100")
    t.long_strike = Decimal("95")
    t.expiration = date(2026, 9, 18)
    return t


@pytest.mark.asyncio
async def test_close_position_invalid_trade_id_raises():
    with pytest.raises(Exception):
        await close_position(ClosePositionRequest(trade_id="not-a-uuid"))


@pytest.mark.asyncio
async def test_close_position_not_found_raises():
    session = _fake_trade_session(None)
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        with pytest.raises(Exception):
            await close_position(ClosePositionRequest(
                trade_id="11111111-1111-1111-1111-111111111111"))


@pytest.mark.asyncio
async def test_close_position_already_closed_raises():
    session = _fake_trade_session(_open_trade(status="closed"))
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        with pytest.raises(Exception):
            await close_position(ClosePositionRequest(
                trade_id="11111111-1111-1111-1111-111111111111"))


@pytest.mark.asyncio
async def test_close_position_invalid_spread_type_raises_400():
    session = _fake_trade_session(_open_trade(spread_type="short_straddle"))
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        with pytest.raises(Exception):
            await close_position(ClosePositionRequest(
                trade_id="11111111-1111-1111-1111-111111111111"))


@pytest.mark.asyncio
async def test_close_position_options_routes_to_close_options_trade():
    session = _fake_trade_session(_open_options_trade(spread_type="put"))
    close_opt_mock = AsyncMock(return_value={
        "trade_id": "11111111-1111-1111-1111-111111111111",
        "ticker": "AAPL", "status": "filled",
    })
    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()), \
         patch("app.services.position_rotation.close_options_trade", new=close_opt_mock), \
         patch("app.services.position_rotation.close_equity_trade") as close_eq_mock, \
         patch.object(td, "_log_execution", new=AsyncMock()):
        out = await close_position(ClosePositionRequest(
            trade_id="11111111-1111-1111-1111-111111111111"))

    close_opt_mock.assert_awaited_once()
    close_eq_mock.assert_not_called()
    assert out["status"] == "filled"


@pytest.mark.asyncio
async def test_close_position_options_missing_strikes_raises_400():
    trade = _open_options_trade()
    trade.short_strike = None
    session = _fake_trade_session(trade)
    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()):
        with pytest.raises(Exception):
            await close_position(ClosePositionRequest(
                trade_id="11111111-1111-1111-1111-111111111111"))


@pytest.mark.asyncio
async def test_close_position_options_broker_rejection_raises_502():
    session = _fake_trade_session(_open_options_trade())
    broker = MagicMock()
    broker.cancel_open_orders = AsyncMock(return_value=0)
    broker.place_order = AsyncMock(return_value=MagicMock(
        status="rejected", order_id=None, fill_price=None,
    ))
    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.broker.broker_factory.get_broker", return_value=broker):
        with pytest.raises(Exception):
            await close_position(ClosePositionRequest(
                trade_id="11111111-1111-1111-1111-111111111111"))


@pytest.mark.asyncio
async def test_close_position_long_submits_sell_and_cancels_bracket():
    """A closing order must never go through _execute_signal's duplicate
    guard — this test proves it's never called — and equity_long closes
    with SELL, not BUY."""
    session = _fake_trade_session(_open_trade(spread_type="equity_long", quantity=10))
    broker = MagicMock()
    broker.cancel_open_orders = AsyncMock(return_value=2)
    # close_equity_trade() sizes and sides the close from the broker's own
    # live position, not the DB's spread_type/quantity — see that
    # function's docstring for the 2026-08-26 incident this guards against.
    broker.get_equity_positions = AsyncMock(
        return_value=[SimpleNamespace(symbol="AAPL", quantity=10)]
    )
    broker.place_equity_order = AsyncMock(return_value=MagicMock(
        status="filled", order_id="ord-1", fill_price=101.5,
    ))
    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch.object(td, "_execute_signal", new=AsyncMock()) as exec_mock, \
         patch.object(td, "_log_execution", new=AsyncMock()), \
         patch("app.services.trade_recorder.trade_recorder.record_exit",
               new=AsyncMock(return_value=True)) as record_mock:
        out = await close_position(ClosePositionRequest(
            trade_id="11111111-1111-1111-1111-111111111111"))

    exec_mock.assert_not_called()
    broker.cancel_open_orders.assert_awaited_once_with("AAPL")
    broker.place_equity_order.assert_awaited_once()
    assert broker.place_equity_order.await_args.kwargs["side"] == "SELL"
    assert broker.place_equity_order.await_args.kwargs["qty"] == 10
    record_mock.assert_awaited_once()
    assert record_mock.await_args.kwargs["cost_to_close"] == 101.5
    assert record_mock.await_args.kwargs["exit_reason"] == "manual"
    assert out["action"] == "SELL"
    assert out["cancelled_open_orders"] == 2


@pytest.mark.asyncio
async def test_close_position_short_submits_buy():
    session = _fake_trade_session(_open_trade(spread_type="equity_short", quantity=5))
    broker = MagicMock()
    broker.cancel_open_orders = AsyncMock(return_value=0)
    broker.get_equity_positions = AsyncMock(
        return_value=[SimpleNamespace(symbol="AAPL", quantity=-5)]
    )
    broker.place_equity_order = AsyncMock(return_value=MagicMock(
        status="submitted", order_id="ord-2", fill_price=None,
    ))
    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch.object(td, "_log_execution", new=AsyncMock()):
        out = await close_position(ClosePositionRequest(
            trade_id="11111111-1111-1111-1111-111111111111"))

    assert broker.place_equity_order.await_args.kwargs["side"] == "BUY"
    assert out["status"] == "submitted"


@pytest.mark.asyncio
async def test_close_position_broker_rejection_raises():
    session = _fake_trade_session(_open_trade())
    broker = MagicMock()
    broker.cancel_open_orders = AsyncMock(return_value=0)
    broker.place_equity_order = AsyncMock(return_value=MagicMock(
        status="rejected", order_id=None, fill_price=None,
    ))
    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.broker.broker_factory.get_broker", return_value=broker):
        with pytest.raises(Exception):
            await close_position(ClosePositionRequest(
                trade_id="11111111-1111-1111-1111-111111111111"))


@pytest.mark.asyncio
async def test_execution_log_limit():
    rows = [MagicMock(payload={"i": i}) for i in range(3)]
    list_result = MagicMock(scalars=MagicMock(return_value=MagicMock(all=lambda: rows)))
    count_result = MagicMock(scalar_one=MagicMock(return_value=5))
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(side_effect=[list_result, count_result])
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        out = await get_execution_log(limit=3)
    assert len(out["log"]) == 3 and out["total"] == 5


# ── handle_signal dispatcher ──────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_handle_signal_manual_noop():
    td.execution_mode_manager._mode = ExecutionMode.MANUAL
    with patch.object(td, "_queue_pending_approval", new=AsyncMock()) as queue_mock:
        await handle_signal({"id": "x", "ticker": "SPY"})   # no error, nothing queued
    queue_mock.assert_not_called()


@pytest.mark.asyncio
async def test_handle_signal_copilot_queues():
    td.execution_mode_manager._mode = ExecutionMode.COPILOT
    with patch.object(td, "_queue_pending_approval", new=AsyncMock()) as queue_mock:
        signal = {"id": "c1", "ticker": "SPY"}
        await handle_signal(signal)
    queue_mock.assert_awaited_once_with(signal)
    td.execution_mode_manager._mode = ExecutionMode.MANUAL


@pytest.mark.asyncio
async def test_handle_signal_autopilot_executes_and_logs_block():
    td.execution_mode_manager._mode = ExecutionMode.AUTOPILOT
    with patch.object(td, "_execute_signal",
                      new=AsyncMock(return_value={"result": "blocked", "reason": "kill_switch"})), \
         patch.object(td, "_log_execution", new=AsyncMock()) as log_mock:
        await handle_signal({"id": "a1", "ticker": "SPY"})
    log_mock.assert_awaited_once()
    assert log_mock.await_args.args[0]["result"] == "blocked"
    td.execution_mode_manager._mode = ExecutionMode.MANUAL


# ── options execution branch of _execute_signal ───────────────────────────────────
def _options_signal():
    return {
        "id": "o1", "ticker": "SPY", "action": "SELL", "asset_type": "options",
        "strategy": "bull_put_spread", "quantity": 1, "confidence": 0.9, "pop": 0.85,
        "signal_score": 0.85, "iv_rank": 45.0, "regime": "normal_mean_revert",
        "spread": {"expiration": "2026-09-18", "short_strike": 450, "long_strike": 445,
                   "option_type": "put", "net_credit": 150.0, "max_loss": 350.0},
    }


def _dup_session(existing=None):
    """Mock DB session for Stage 3 duplicate guard.

    `existing` may be:
      - None / [] → no open trades
      - a Trade-like object or list of them
      - legacy ``("trade-id",)`` → synthesize an open SPY options trade
    """
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    if existing is None or existing == []:
        rows = []
    elif (
        isinstance(existing, tuple)
        and existing
        and not hasattr(existing[0], "underlying")
    ):
        t = MagicMock()
        t.id = existing[0]
        t.underlying = "SPY"
        t.spread_type = "bull_put_spread"
        t.strategy = "bull_put_spread"
        t.status = "open"
        rows = [t]
    elif isinstance(existing, (list, tuple)):
        rows = list(existing)
    else:
        rows = [existing]
    result = MagicMock()
    result.scalars.return_value = MagicMock(all=lambda: rows)
    result.first = MagicMock(return_value=rows[0] if rows else None)
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_options_execution_submits_and_records(stub_position_claim):
    broker = MagicMock()
    broker.place_order = AsyncMock(return_value=MagicMock(order_id="ORD-9", status="submitted"))
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.api.routes.trade_desk._strategy_health_for", new=AsyncMock(return_value=None)), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=_dup_session(None)), \
         patch("app.services.trade_recorder.trade_recorder.record_fill",
               new=AsyncMock(return_value="trade-1")):
        res = await _execute_signal(_options_signal(), approved_by="autopilot")
    assert res["result"] == "submitted" and res["asset_type"] == "options"
    broker.place_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_options_execution_zero_size_skipped():
    sig = _options_signal()
    sig["quantity"] = 0
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.core.database.AsyncSessionLocal", return_value=_dup_session(None)), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()):
        res = await _execute_signal(sig, approved_by="manual")
    assert res["result"] == "skipped" and "zero_size" in res["reason"]


@pytest.mark.asyncio
async def test_market_closed_blocks_order():
    with patch("app.utils.market_hours.is_market_open", return_value=False), \
         patch("app.utils.market_hours.market_status",
               return_value={"reason": "weekend", "now_et": "Sat 10:00"}), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False):
        res = await _execute_signal(_options_signal(), approved_by="manual")
    assert res["result"] == "blocked" and "market_closed" in res["reason"]


@pytest.mark.asyncio
async def test_options_record_failure_still_submits(stub_position_claim):
    # broker fills but DB record fails (None) → still 'submitted', critical-log path
    broker = MagicMock()
    broker.place_order = AsyncMock(return_value=MagicMock(order_id="ORD-X", status="submitted"))
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=_dup_session(None)), \
         patch("app.services.trade_recorder.trade_recorder.record_fill",
               new=AsyncMock(return_value=None)):
        res = await _execute_signal(_options_signal(), approved_by="manual")
    assert res["result"] == "submitted"


@pytest.mark.asyncio
async def test_options_place_order_timeout_records_pending_not_lost(stub_position_claim):
    """The coordinator's wait_for can time out while the real IBKR call
    (shielded, still running) later fills — this must not be a silent lost
    fill: a pending Trade row has to get written so _poll_fills() can later
    promote or cancel it, and the caller must see an honest result instead
    of a generic error."""
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()), \
         patch("app.core.database.AsyncSessionLocal", return_value=_dup_session(None)), \
         patch.object(td.ibkr_coordinator, "submit",
                      new=AsyncMock(side_effect=asyncio.TimeoutError("timed out"))), \
         patch("app.services.trade_recorder.trade_recorder.record_fill",
               new=AsyncMock(return_value="trade-pending-1")) as record_mock:
        res = await _execute_signal(_options_signal(), approved_by="manual")

    assert res["result"] == "pending_confirmation"
    assert res["asset_type"] == "options"
    record_mock.assert_awaited_once()
    assert record_mock.await_args.kwargs["status"] == "pending"
    assert record_mock.await_args.kwargs["dispatch_id"] == "o1"


@pytest.mark.asyncio
async def test_options_place_order_timeout_and_record_failure_logs_critical(stub_position_claim):
    """Belt-and-suspenders: even the pending-row write can fail (DB down) —
    must not raise, just log critical, since a real order may be in flight
    at the broker with zero remaining trace of it."""
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()), \
         patch("app.core.database.AsyncSessionLocal", return_value=_dup_session(None)), \
         patch.object(td.ibkr_coordinator, "submit",
                      new=AsyncMock(side_effect=asyncio.TimeoutError("timed out"))), \
         patch("app.services.trade_recorder.trade_recorder.record_fill",
               new=AsyncMock(return_value=None)):
        res = await _execute_signal(_options_signal(), approved_by="manual")

    assert res["result"] == "pending_confirmation"


@pytest.mark.asyncio
async def test_equity_place_order_timeout_records_pending_not_lost(stub_position_claim):
    broker = MagicMock()
    broker.get_latest_quote = AsyncMock(return_value=MagicMock(ask_price=150.5, bid_price=150.0))
    sig = _equity_signal(source="equity_desk_composer", confidence=None, kelly_fraction=None)
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.api.routes.trade_desk._strategy_health_for", new=AsyncMock(return_value=None)), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=_dup_session(None)), \
         patch.object(td.ibkr_coordinator, "submit",
                      new=AsyncMock(side_effect=asyncio.TimeoutError("timed out"))), \
         patch("app.services.trade_recorder.trade_recorder.record_fill",
               new=AsyncMock(return_value="trade-pending-2")) as record_mock:
        res = await _execute_signal(sig, approved_by="user")

    assert res["result"] == "pending_confirmation"
    assert res["asset_type"] == "equity"
    record_mock.assert_awaited_once()
    assert record_mock.await_args.kwargs["status"] == "pending"
    assert record_mock.await_args.kwargs["dispatch_id"] == "e1"
    assert record_mock.await_args.kwargs["option_type"] == "equity_long"


@pytest.mark.asyncio
async def test_duplicate_open_trade_skipped():
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.core.database.AsyncSessionLocal", return_value=_dup_session(existing=("trade-x",))), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()):
        res = await _execute_signal(_options_signal(), approved_by="manual")
    assert res["result"] == "skipped" and "already_open" in res["reason"]


@pytest.mark.asyncio
async def test_duplicate_guard_allows_other_asset_class_same_underlying(stub_position_claim):
    """Open SPY equity must not block a new SPY options signal (and vice versa)."""
    equity_open = MagicMock()
    equity_open.id = "eq-1"
    equity_open.underlying = "SPY"
    equity_open.spread_type = "equity_long"
    equity_open.strategy = "equity"
    equity_open.status = "open"

    broker = MagicMock()
    broker.place_order = AsyncMock(return_value=MagicMock(order_id="ORD-opt", status="submitted"))
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.api.routes.trade_desk._strategy_health_for", new=AsyncMock(return_value=None)), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=_dup_session(existing=[equity_open])), \
         patch("app.services.trade_recorder.trade_recorder.record_fill",
               new=AsyncMock(return_value="trade-opt")):
        res = await _execute_signal(_options_signal(), approved_by="manual")
    assert res["result"] == "submitted"
    broker.place_order.assert_awaited_once()


def _dup_and_cooldown_session(dup_existing=None, cooldown_existing=None):
    """Four sequential AsyncSessionLocal() calls happen before Stage 3b can
    run: Stage 2b's own portfolio-risk-state read (check_execution_portfolio
    -> load_portfolio_risk_state, independent of the _fetch_portfolio_state
    mock used for Stage 2), then Stage 3's duplicate guard TWICE — once
    before taking the position claim and once after winning it, because a
    trade can be recorded by another caller in between — then Stage 3b
    (closed/cooldown). session.execute must answer them in that order."""
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    dup_rows = [] if not dup_existing else (
        dup_existing if isinstance(dup_existing, list) else [dup_existing]
    )
    cd_rows = [] if not cooldown_existing else (
        cooldown_existing if isinstance(cooldown_existing, list) else [cooldown_existing]
    )
    portfolio_result = MagicMock()
    portfolio_result.scalars.return_value = MagicMock(all=lambda: [])
    dup_result = MagicMock()
    dup_result.scalars.return_value = MagicMock(all=lambda: dup_rows)
    cd_result = MagicMock()
    cd_result.scalars.return_value = MagicMock(all=lambda: cd_rows)
    session.execute = AsyncMock(
        side_effect=[portfolio_result, dup_result, dup_result, cd_result])
    return session


def _closed_trade(underlying="SPY", asset_class="options", exit_date=None):
    t = MagicMock()
    t.underlying = underlying
    t.status = "closed"
    t.exit_date = exit_date or datetime.now(timezone.utc)
    if asset_class == "equity":
        t.spread_type = "equity_long"
        t.strategy = "equity"
    else:
        t.spread_type = "bull_put_spread"
        t.strategy = "bull_put_spread"
    return t


@pytest.mark.asyncio
async def test_cooldown_blocks_recently_closed_same_ticker_same_asset_class(stub_position_claim):
    closed = _closed_trade(exit_date=datetime.now(timezone.utc) - timedelta(minutes=30))
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.core.config.settings.position_cooldown_hours", 2), \
         patch("app.core.database.AsyncSessionLocal",
               return_value=_dup_and_cooldown_session(cooldown_existing=closed)), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()):
        res = await _execute_signal(_options_signal(), approved_by="manual")
    assert res["result"] == "skipped" and "cooldown_active" in res["reason"]


@pytest.mark.asyncio
async def test_cooldown_allows_different_asset_class_same_underlying(stub_position_claim):
    """A closed SPY equity trade must not cooldown-block a new SPY options
    signal (and vice versa) — matches Stage 3's own asset-class carve-out."""
    closed_equity = _closed_trade(
        asset_class="equity", exit_date=datetime.now(timezone.utc) - timedelta(minutes=5),
    )
    broker = MagicMock()
    broker.place_order = AsyncMock(return_value=MagicMock(order_id="ORD-cd", status="submitted"))
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.core.config.settings.position_cooldown_hours", 2), \
         patch("app.core.database.AsyncSessionLocal",
               return_value=_dup_and_cooldown_session(cooldown_existing=closed_equity)), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.services.trade_recorder.trade_recorder.record_fill",
               new=AsyncMock(return_value="trade-cd")):
        res = await _execute_signal(_options_signal(), approved_by="manual")
    assert res["result"] == "submitted"
    broker.place_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_cooldown_allows_close_older_than_window(stub_position_claim):
    closed = _closed_trade(exit_date=datetime.now(timezone.utc) - timedelta(hours=3))
    broker = MagicMock()
    broker.place_order = AsyncMock(return_value=MagicMock(order_id="ORD-old", status="submitted"))
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.core.config.settings.position_cooldown_hours", 2), \
         patch("app.core.database.AsyncSessionLocal",
               return_value=_dup_and_cooldown_session(cooldown_existing=closed)), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.services.trade_recorder.trade_recorder.record_fill",
               new=AsyncMock(return_value="trade-old")):
        res = await _execute_signal(_options_signal(), approved_by="manual")
    assert res["result"] == "submitted"


@pytest.mark.asyncio
async def test_cooldown_disabled_skips_check(stub_position_claim):
    closed = _closed_trade(exit_date=datetime.now(timezone.utc) - timedelta(minutes=1))
    broker = MagicMock()
    broker.place_order = AsyncMock(return_value=MagicMock(order_id="ORD-off", status="submitted"))
    session = _dup_and_cooldown_session(cooldown_existing=closed)
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.core.config.settings.position_cooldown_hours", 0), \
         patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.services.trade_recorder.trade_recorder.record_fill",
               new=AsyncMock(return_value="trade-off")):
        res = await _execute_signal(_options_signal(), approved_by="manual")
    assert res["result"] == "submitted"
    # Stage 2b's portfolio-state read + Stage 3's dup check before and after
    # the claim. Stage 3b's DB call never happens: the cooldown is disabled.
    assert session.execute.call_count == 3


@pytest.mark.asyncio
async def test_cooldown_check_db_error_fails_closed(stub_position_claim):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    portfolio_result = MagicMock()
    portfolio_result.scalars.return_value = MagicMock(all=lambda: [])
    dup_result = MagicMock()
    dup_result.scalars.return_value = MagicMock(all=lambda: [])
    # portfolio read, then Stage 3's duplicate read before AND after the
    # claim, then Stage 3b's cooldown read — which is the one that fails.
    session.execute = AsyncMock(
        side_effect=[portfolio_result, dup_result, dup_result, RuntimeError("db down")])
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.core.config.settings.position_cooldown_hours", 2), \
         patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()):
        res = await _execute_signal(_options_signal(), approved_by="manual")
    assert res["result"] == "blocked" and "cooldown_check_error" in res["reason"]


# ── Stage 1c: Equity Desk composer confidence-gate carve-out ──────────────────────
def _equity_signal(**overrides):
    sig = {
        "id": "e1", "ticker": "AAPL", "action": "BUY", "asset_type": "equity",
        "trade_plan": {"shares": 10, "entry_price": 150.0, "stop_price": 147.0,
                        "target_price": 156.0},
        "source": "scan_engine", "confidence": 0.1, "kelly_fraction": 0.1,
    }
    sig.update(overrides)
    return sig


@pytest.mark.asyncio
async def test_equity_desk_composer_order_bypasses_confidence_gate(stub_position_claim):
    """Regression: a human-composed Equity Desk order (no AI signal behind
    it) must not be silently blocked by the AI-signal confidence gate —
    every mode's min_confidence exceeds a fabricated 0.5, so this order
    would previously be blocked unconditionally."""
    broker = MagicMock()
    broker.get_latest_quote = AsyncMock(return_value=MagicMock(ask_price=150.5, bid_price=150.0))
    broker.place_equity_order = AsyncMock(return_value=MagicMock(order_id="ORD-1", status="filled"))
    sig = _equity_signal(source="equity_desk_composer", confidence=None, kelly_fraction=None)
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.api.routes.trade_desk._strategy_health_for", new=AsyncMock(return_value=None)), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=_dup_session(None)), \
         patch("app.services.trade_recorder.trade_recorder.record_fill",
               new=AsyncMock(return_value="trade-e1")):
        res = await _execute_signal(sig, approved_by="user")
    assert res["result"] == "submitted"
    broker.place_equity_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_scan_signal_low_confidence_still_blocked_when_approved_by_user():
    """The carve-out must be scoped to equity_desk_composer only — a real
    AI scan signal with genuinely low confidence, approved by a human,
    must still be caught by the frequency controller."""
    sig = _equity_signal(source="scan_engine", confidence=0.1)
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False):
        res = await _execute_signal(sig, approved_by="user")
    assert res["result"] == "blocked"
    assert "below_min_confidence" in res["reason"]


# ── 0DTE autopilot gate ──────────────────────────────────────────────────────
def _options_signal_dte(dte: int):
    sig = _options_signal()
    sig["spread"]["dte"] = dte
    return sig


@pytest.mark.asyncio
async def test_0dte_autopilot_signal_blocked_before_broker():
    with patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()) as get_broker_mock:
        res = await _execute_signal(_options_signal_dte(0), approved_by="autopilot")
    assert res["result"] == "blocked"
    assert res["reason"] == "0dte_autopilot_disabled"
    get_broker_mock.assert_not_called()


@pytest.mark.asyncio
async def test_1dte_autopilot_signal_also_blocked():
    with patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False):
        res = await _execute_signal(_options_signal_dte(1), approved_by="autopilot")
    assert res["result"] == "blocked"
    assert res["reason"] == "0dte_autopilot_disabled"


@pytest.mark.asyncio
async def test_2dte_autopilot_signal_not_blocked_by_0dte_gate(stub_position_claim):
    """2 DTE clears the hard gate — must reach broker submission, not be
    silently swallowed by an off-by-one in the dte<=1 comparison."""
    broker = MagicMock()
    broker.place_order = AsyncMock(return_value=MagicMock(order_id="ORD-2DTE", status="submitted"))
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.api.routes.trade_desk._strategy_health_for", new=AsyncMock(return_value=None)), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=_dup_session(None)), \
         patch("app.services.trade_recorder.trade_recorder.record_fill",
               new=AsyncMock(return_value="trade-2dte")):
        res = await _execute_signal(_options_signal_dte(2), approved_by="autopilot")
    assert res["result"] == "submitted"
    broker.place_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_0dte_manual_approval_not_blocked_by_autopilot_gate(stub_position_claim):
    """0DTE stays available with a human in the loop — the gate only fires
    for approved_by=="autopilot", matching the UI's "Copilot or manual
    only" language."""
    broker = MagicMock()
    broker.place_order = AsyncMock(return_value=MagicMock(order_id="ORD-MANUAL-0DTE", status="submitted"))
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.utils.market_hours.minutes_to_close", return_value=None), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=_dup_session(None)), \
         patch("app.services.trade_recorder.trade_recorder.record_fill",
               new=AsyncMock(return_value="trade-manual-0dte")):
        res = await _execute_signal(_options_signal_dte(0), approved_by="manual")
    assert res["result"] == "submitted"
    broker.place_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_0dte_gate_does_not_apply_to_equity_signals(stub_position_claim):
    """Equity signals have no spread.dte at all — the gate must be a no-op
    for asset_type != "options", not raise on a missing key."""
    sig = _equity_signal(source="equity_desk_composer", confidence=0.05, action="BUY")
    broker = MagicMock()
    broker.place_equity_order = AsyncMock(return_value=MagicMock(order_id="ORD-EQ", status="submitted"))
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=_dup_session(None)), \
         patch("app.services.trade_recorder.trade_recorder.record_fill",
               new=AsyncMock(return_value="trade-eq-0dte")):
        res = await _execute_signal(sig, approved_by="autopilot")
    assert res["result"] == "submitted"


# ── Liquidity / gamma / close-proximity gates (Stage 1b3) ────────────────────
def _options_signal_liquidity(**spread_overrides):
    sig = _options_signal()
    sig["spread"].update(spread_overrides)
    return sig


@pytest.mark.asyncio
async def test_liquidity_gate_blocks_wide_spread():
    sig = _options_signal_liquidity(bid_ask_width_pct=0.20)
    with patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()) as get_broker_mock:
        res = await _execute_signal(sig, approved_by="manual")
    assert res["result"] == "blocked"
    assert "spread_too_wide" in res["reason"]
    get_broker_mock.assert_not_called()


@pytest.mark.asyncio
async def test_liquidity_gate_blocks_low_open_interest():
    sig = _options_signal_liquidity(open_interest=10)
    with patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()) as get_broker_mock:
        res = await _execute_signal(sig, approved_by="manual")
    assert res["result"] == "blocked"
    assert "open_interest_too_low" in res["reason"]
    get_broker_mock.assert_not_called()


@pytest.mark.asyncio
async def test_liquidity_gate_blocks_0dte_near_close():
    sig = _options_signal_liquidity(dte=0)
    with patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.utils.market_hours.minutes_to_close", return_value=10.0), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()) as get_broker_mock:
        res = await _execute_signal(sig, approved_by="manual")
    assert res["result"] == "blocked"
    assert "0dte_near_close" in res["reason"]
    get_broker_mock.assert_not_called()


@pytest.mark.asyncio
async def test_liquidity_gate_0dte_check_skipped_when_not_0dte(stub_position_claim):
    """minutes_to_close < 30 must not block a >1 DTE signal — the close-
    proximity check is scoped to dte<=1 only."""
    sig = _options_signal_liquidity(dte=5)
    broker = MagicMock()
    broker.place_order = AsyncMock(return_value=MagicMock(order_id="ORD-NOT0DTE", status="submitted"))
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.utils.market_hours.minutes_to_close", return_value=10.0), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=_dup_session(None)), \
         patch("app.services.trade_recorder.trade_recorder.record_fill",
               new=AsyncMock(return_value="trade-not0dte")):
        res = await _execute_signal(sig, approved_by="manual")
    assert res["result"] == "submitted"


@pytest.mark.asyncio
async def test_liquidity_gate_fails_open_on_missing_data(stub_position_claim):
    """The base _options_signal() fixture has no bid_ask_width_pct/
    open_interest/gamma keys at all (the yfinance/Black-Scholes fallback
    shape) — must reach broker submission, not be blocked for missing
    fields that were never fabricated."""
    sig = _options_signal()
    broker = MagicMock()
    broker.place_order = AsyncMock(return_value=MagicMock(order_id="ORD-NODATA", status="submitted"))
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=_dup_session(None)), \
         patch("app.services.trade_recorder.trade_recorder.record_fill",
               new=AsyncMock(return_value="trade-nodata")):
        res = await _execute_signal(sig, approved_by="manual")
    assert res["result"] == "submitted"


@pytest.mark.asyncio
async def test_liquidity_gate_passes_healthy_spread(stub_position_claim):
    sig = _options_signal_liquidity(bid_ask_width_pct=0.05, open_interest=500, gamma=0.01)
    broker = MagicMock()
    broker.place_order = AsyncMock(return_value=MagicMock(order_id="ORD-HEALTHY", status="submitted"))
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=_dup_session(None)), \
         patch("app.services.trade_recorder.trade_recorder.record_fill",
               new=AsyncMock(return_value="trade-healthy")):
        res = await _execute_signal(sig, approved_by="manual")
    assert res["result"] == "submitted"


# ── margin guard: stale account values must not clear it ─────────────────────
# The guard reads figures out of IBKR's locally-cached push stream. A dead
# stream still answers, with the complete margin picture from whenever it
# died — so the guard's one dangerous failure is reading "margin is fine" off
# numbers that predate the blow-up it exists to catch.

def _submit_router(acct):
    """Stand in for ibkr_coordinator.submit.

    The coordinator is the single door for ACCOUNT_SUMMARY *and* PLACE_ORDER,
    so a blanket stub would hand the order path an AccountSummary. Route by
    req_type and let anything that isn't an account read run for real.
    """
    async def _submit(priority, fn, *args, req_type=None, **kwargs):
        if req_type == "ACCOUNT_SUMMARY":
            return acct
        res = fn(*args) if args else fn()
        return await res if hasattr(res, "__await__") else res
    return AsyncMock(side_effect=_submit)


def _acct_summary(*, is_stale: bool, age: float, maint: float = 10_000.0):
    from decimal import Decimal
    from app.broker.broker_interface import AccountSummary
    return AccountSummary(
        account_id="DU123456",
        net_liquidation=Decimal("100000"),
        cash_balance=Decimal("50000"),
        buying_power=Decimal("200000"),
        maintenance_margin=Decimal(str(maint)),
        excess_liquidity=Decimal("50000"),
        init_margin=Decimal("20000"),
        data_age_seconds=age,
        is_stale=is_stale,
    )


@pytest.mark.asyncio
async def test_margin_guard_is_skipped_when_account_values_are_stale(stub_position_claim):
    """Stale figures are treated as absent, not as an all-clear.

    evaluate_margin must never even run: a frozen snapshot can be wrong in
    either direction — a false 'fine' that lets a trade through during a real
    margin event, or a false 'critical' that blocks after recovery.
    """
    broker = MagicMock()
    broker.place_order = AsyncMock(return_value=MagicMock(order_id="ORD-1", status="submitted"))

    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.api.routes.trade_desk._strategy_health_for", new=AsyncMock(return_value=None)), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=_dup_session(None)), \
         patch("app.api.routes.trade_desk.ibkr_coordinator.submit",
               new=_submit_router(_acct_summary(is_stale=True, age=1800.0, maint=99_000.0))), \
         patch("app.services.margin_monitor.evaluate_margin") as eval_mock, \
         patch("app.services.trade_recorder.trade_recorder.record_fill",
               new=AsyncMock(return_value="trade-1")):
        res = await _execute_signal(_options_signal(), approved_by="autopilot")

    eval_mock.assert_not_called()
    # maint=99k against nl=100k would be a hard 'critical' block if evaluated —
    # proving the skip is what let this through, not a benign margin picture.
    assert res["result"] == "submitted"


@pytest.mark.asyncio
async def test_margin_guard_still_blocks_on_fresh_critical_figures(stub_position_claim):
    """Regression pin for the other direction: the staleness check must not
    have quietly disabled the guard for live data."""
    broker = MagicMock()
    broker.place_order = AsyncMock(return_value=MagicMock(order_id="ORD-2", status="submitted"))

    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=_clean())), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.api.routes.trade_desk._strategy_health_for", new=AsyncMock(return_value=None)), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=_dup_session(None)), \
         patch("app.api.routes.trade_desk.ibkr_coordinator.submit",
               new=_submit_router(_acct_summary(is_stale=False, age=12.0, maint=99_000.0))):
        res = await _execute_signal(_options_signal(), approved_by="autopilot")

    assert res["result"] == "blocked"
    assert "margin_critical" in res["reason"]
    broker.place_order.assert_not_awaited()
