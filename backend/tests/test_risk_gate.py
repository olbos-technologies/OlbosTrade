"""
Batch-1 acceptance tests — single fail-closed order pipeline.

Asserts:
  - Signal/autopilot order with risk-limit breach is rejected; no order reaches broker.
  - Manual order with a breach is rejected the same way (no manual bypass).
  - Copilot-approved order with a breach is rejected the same way.
  - When the guardrail DB read raises, the gate refuses the trade (fail closed).
  - When sizing returns 0 contracts, no order is placed (paper_trader path).
  - A clean order passes all five stages IN ORDER and reaches fill-confirmed recording.
  - There is no second guardrail path left in handle_signal.

Run with: pytest tests/test_risk_gate.py -v
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# These tests exercise the risk gate, not market hours. Force the market-hours
# guard open so _execute_signal reaches the guardrail/sizing stages regardless of
# when the suite runs. (is_market_open is imported inside _execute_signal at call
# time, so patching the module attribute is sufficient.)
@pytest.fixture(autouse=True)
def _market_always_open():
    with patch("app.utils.market_hours.is_market_open", return_value=True):
        yield


# ── Signal fixtures ────────────────────────────────────────────────────────────

def _equity_signal(ticker="AAPL", shares=10, limit_price=150.0):
    # Realistic autopilot signal: high confidence + positive reward:risk so it
    # clears the Trade Frequency Controller and reaches the stage under test.
    return {
        "id":         "sig-001",
        "ticker":     ticker,
        "action":     "BUY",
        "asset_type": "equity",
        "confidence": 0.95,
        "trade_plan": {"shares": shares, "entry_price": limit_price,
                       "stop_price": None, "target_price": None,
                       "risk_reward": 2.0},
        "indicators": {"volume_ratio": 1.5},
        "signal_score": 0.8,
        "iv_rank": 30.0,
        "regime": "normal",
    }


def _options_signal(ticker="SPY", quantity=1):
    return {
        "id":         "sig-002",
        "ticker":     ticker,
        "action":     "SELL",
        "asset_type": "options",
        "strategy":   "bull_put_spread",
        "quantity":   quantity,
        "confidence": 0.92,
        "spread": {
            "expiration":   "2025-06-20",
            "short_strike": 450,
            "long_strike":  445,
            "option_type":  "put",
            "net_credit":   1.50,
            "max_loss":     3.50,
        },
        "signal_score": 0.75,
        "iv_rank": 45.0,
        "regime": "normal",
    }


# ── Portfolio state helpers ────────────────────────────────────────────────────

def _clean_portfolio():
    """PortfolioState that passes all guardrails."""
    from app.services.guardrails import PortfolioState
    return PortfolioState(
        current_value=100_000.0,
        starting_capital=100_000.0,
        daily_pnl=0.0,
        weekly_pnl=0.0,
        monthly_pnl=0.0,
        consecutive_losses=0,
        trades_today=0,
    )


def _breached_portfolio():
    """PortfolioState that breaches the daily loss limit."""
    from app.services.guardrails import PortfolioState
    from app.core.config import settings
    limit = settings.max_daily_loss_pct
    return PortfolioState(
        current_value=100_000.0,
        starting_capital=100_000.0,
        daily_pnl=-(100_000.0 * limit + 500),  # just over the limit
        weekly_pnl=0.0,
        monthly_pnl=0.0,
        consecutive_losses=0,
        trades_today=0,
    )


# ── Helpers to mock the gate's DB fetch ───────────────────────────────────────

def _patch_fetch(portfolio_state):
    """Patch _fetch_portfolio_state to return a specific PortfolioState."""
    return patch(
        "app.api.routes.trade_desk._fetch_portfolio_state",
        new=AsyncMock(return_value=portfolio_state),
    )


def _patch_fetch_raise(exc=None):
    """Patch _fetch_portfolio_state to raise RiskGateError (fail-closed scenario)."""
    from app.api.routes.trade_desk import RiskGateError
    return patch(
        "app.api.routes.trade_desk._fetch_portfolio_state",
        new=AsyncMock(side_effect=exc or RiskGateError("simulated DB failure")),
    )


def _mock_broker(order_id="ORD-1"):
    broker = MagicMock()
    order_result = MagicMock(order_id=order_id, status="submitted",
                             fill_price=None, message=None)
    broker.place_equity_order = AsyncMock(return_value=order_result)
    broker.place_order = AsyncMock(return_value=order_result)
    broker.get_account_summary = AsyncMock(return_value=MagicMock(net_liquidation=100_000.0))
    return broker


def _mock_recorder(success=True):
    recorder = MagicMock()
    recorder.record_fill = AsyncMock(return_value="trade-uuid-1" if success else None)
    return recorder


# ══════════════════════════════════════════════════════════════════════════════
# 1. Signal/autopilot order with risk breach → blocked (no order placed)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_signal_blocked_on_guardrail_breach():
    from app.api.routes.trade_desk import _execute_signal
    broker = _mock_broker()

    with _patch_fetch(_breached_portfolio()), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=broker):

        result = await _execute_signal(_equity_signal(), approved_by="autopilot")

    assert result["result"] == "blocked"
    assert "guardrail" in result["reason"]
    broker.place_equity_order.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 2. Manual order with breach → blocked the SAME way (no manual bypass)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_manual_order_blocked_on_guardrail_breach():
    from app.api.routes.trade_desk import _execute_signal
    broker = _mock_broker()

    with _patch_fetch(_breached_portfolio()), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=broker):

        result = await _execute_signal(_equity_signal(), approved_by="manual")

    assert result["result"] == "blocked"
    assert "guardrail" in result["reason"]
    broker.place_equity_order.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 3. Copilot-approved order with breach → blocked the SAME way
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_copilot_approved_blocked_on_guardrail_breach():
    """approve_signal() → _execute_signal() must go through the same gate."""
    from app.api.routes.trade_desk import _execute_signal
    broker = _mock_broker()

    with _patch_fetch(_breached_portfolio()), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=broker):

        # "user" is what approve_signal() passes as approved_by
        result = await _execute_signal(_equity_signal(), approved_by="user")

    assert result["result"] == "blocked"
    broker.place_equity_order.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 4. DB read raises → gate refuses (fail closed)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_db_read_failure_is_fail_closed():
    from app.api.routes.trade_desk import _execute_signal, RiskGateError
    broker = _mock_broker()

    with _patch_fetch_raise(RiskGateError("DB connection refused")), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=broker):

        result = await _execute_signal(_equity_signal(), approved_by="autopilot")

    assert result["result"] == "blocked"
    assert "risk_gate_error" in result["reason"]
    broker.place_equity_order.assert_not_called()


@pytest.mark.asyncio
async def test_wrong_column_exception_is_fail_closed():
    """The old realized_pnl/closed_at query raised AttributeError every cycle
    and was swallowed, silently permitting trades. Simulate that exception."""
    from app.api.routes.trade_desk import _execute_signal, RiskGateError
    broker = _mock_broker()

    db_exc = RiskGateError("AttributeError: type object 'Trade' has no attribute 'realized_pnl'")

    with _patch_fetch_raise(db_exc), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=broker):

        result = await _execute_signal(_equity_signal(), approved_by="autopilot")

    assert result["result"] == "blocked"
    broker.place_equity_order.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 5. Sizing returns 0 → trade is skipped, no order placed (paper_trader path)
# ══════════════════════════════════════════════════════════════════════════════

def test_calculate_position_size_returns_zero_for_tiny_budget():
    """RiskManager must return 0 (not 1) when budget can't afford one contract."""
    from app.services.risk_manager import RiskManager
    rm = RiskManager()
    # portfolio_value=100, risk_pct=0.02 → target_risk=$2; max_loss=500 → 0 contracts
    result = rm.calculate_position_size(
        portfolio_value=100.0,
        max_loss_per_spread=500.0,
        risk_pct=0.02,
    )
    assert result == 0


@pytest.mark.asyncio
async def test_zero_shares_skipped_in_execute_signal():
    """_execute_signal skips equity orders when shares == 0."""
    from app.api.routes.trade_desk import _execute_signal
    signal = _equity_signal(shares=0)
    broker = _mock_broker()

    with _patch_fetch(_clean_portfolio()), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=broker):

        result = await _execute_signal(signal, approved_by="autopilot")

    assert result["result"] == "skipped"
    assert result["reason"] == "zero_size"
    broker.place_equity_order.assert_not_called()


@pytest.mark.asyncio
async def test_zero_contracts_skipped_in_execute_signal():
    """_execute_signal skips options orders when quantity == 0."""
    from app.api.routes.trade_desk import _execute_signal
    signal = _options_signal(quantity=0)
    broker = _mock_broker()

    with _patch_fetch(_clean_portfolio()), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=broker):

        result = await _execute_signal(signal, approved_by="autopilot")

    assert result["result"] == "skipped"
    assert result["reason"] == "zero_size"
    broker.place_order.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 6. Clean order passes all five stages IN ORDER
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_clean_order_passes_all_stages_in_order(stub_position_claim):
    """
    A clean signal must:
      kill_switch(False) → portfolio_state fetched → guardrail passes →
      duplicate check passes → broker.place_equity_order called →
      trade_recorder.record_fill called.
    """
    from app.api.routes.trade_desk import _execute_signal

    call_order: list[str] = []

    async def _fetch_ps():
        call_order.append("fetch_portfolio_state")
        return _clean_portfolio()

    broker = MagicMock()
    order_result = MagicMock(order_id="ORD-clean", status="submitted")
    async def _place(**kwargs):
        call_order.append("broker.place_equity_order")
        return order_result
    broker.place_equity_order = _place
    broker.get_account_summary = AsyncMock(return_value=MagicMock(net_liquidation=100_000.0))

    recorder = MagicMock()
    async def _record_fill(**kwargs):
        call_order.append("trade_recorder.record_fill")
        return "trade-uuid"
    recorder.record_fill = _record_fill

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=MagicMock(first=lambda: None))

    with patch("app.api.routes.trade_desk._fetch_portfolio_state", new=_fetch_ps), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.services.trade_recorder.trade_recorder", recorder), \
         patch("app.core.database.AsyncSessionLocal", return_value=mock_session):

        result = await _execute_signal(_equity_signal(), approved_by="autopilot")

    assert result["result"] == "submitted"
    assert "fetch_portfolio_state" in call_order
    assert "broker.place_equity_order" in call_order
    assert "trade_recorder.record_fill" in call_order

    # Verify order: portfolio fetch before broker, broker before recorder
    idx_fetch  = call_order.index("fetch_portfolio_state")
    idx_broker = call_order.index("broker.place_equity_order")
    idx_record = call_order.index("trade_recorder.record_fill")
    assert idx_fetch < idx_broker < idx_record


# ══════════════════════════════════════════════════════════════════════════════
# 7. No second guardrail path left in handle_signal
# ══════════════════════════════════════════════════════════════════════════════

def test_no_second_guardrail_block_in_handle_signal():
    """
    handle_signal's autopilot branch must NOT contain its own inline guardrail
    logic (the old realized_pnl/closed_at block). Only _execute_signal holds the gate.
    """
    import inspect
    import app.api.routes.trade_desk as td

    src = inspect.getsource(td.handle_signal)

    # These column names existed only in the now-deleted inline block
    assert "realized_pnl" not in src, (
        "handle_signal still references Trade.realized_pnl — delete the inline guardrail block"
    )
    assert "closed_at" not in src, (
        "handle_signal still references Trade.closed_at — delete the inline guardrail block"
    )
    # The old pattern of falling back to zeros must be gone
    assert "fall back to zeros" not in src, (
        "handle_signal still has the fail-open 'fall back to zeros' comment"
    )


def test_handle_signal_autopilot_delegates_to_execute_signal():
    """handle_signal for autopilot must call _execute_signal, not place orders directly."""
    import inspect
    import app.api.routes.trade_desk as td

    src = inspect.getsource(td.handle_signal)

    assert "_execute_signal" in src, (
        "handle_signal must delegate to _execute_signal for autopilot path"
    )
    # Broker should never be called directly from handle_signal
    assert "place_equity_order" not in src
    assert "place_order" not in src


# ══════════════════════════════════════════════════════════════════════════════
# 8. Kill switch is checked first, before DB read
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_kill_switch_checked_before_db_read():
    """Kill switch must short-circuit before _fetch_portfolio_state is called."""
    from app.api.routes.trade_desk import _execute_signal
    fetch_mock = AsyncMock()

    with patch("app.api.routes.trade_desk._fetch_portfolio_state", fetch_mock), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=True):

        result = await _execute_signal(_equity_signal(), approved_by="autopilot")

    assert result["result"] == "blocked"
    assert result["reason"] == "kill_switch"
    fetch_mock.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# 9. _fetch_portfolio_state uses correct Trade columns
# ══════════════════════════════════════════════════════════════════════════════

def test_fetch_portfolio_state_uses_correct_columns():
    """_fetch_portfolio_state must query Trade.pnl and Trade.exit_date, not the
    non-existent realized_pnl / closed_at columns."""
    import ast
    import inspect
    import app.api.routes.trade_desk as td

    src = inspect.getsource(td._fetch_portfolio_state)

    # Parse the AST to check attribute accesses in code only (skip docstring)
    tree = ast.parse(src)
    attrs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            attrs.add(node.attr)

    assert "pnl" in attrs, "_fetch_portfolio_state must access Trade.pnl"
    assert "exit_date" in attrs, "_fetch_portfolio_state must access Trade.exit_date"
    assert "realized_pnl" not in attrs, "realized_pnl column does not exist on Trade"
    assert "closed_at" not in attrs, "closed_at column does not exist on Trade"
