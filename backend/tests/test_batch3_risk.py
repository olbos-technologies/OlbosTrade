"""
Batch-3 acceptance tests — risk controls & data integrity.

Asserts:
  - Sizing returns 0 → no trade (paper_trader + _execute_signal paths).
  - In capital-preservation mode, disallowed strategies are blocked; allowed pass with half-size.
  - Scalper mode's risk_per_trade_pct actually reaches position sizing.
  - A doubled same-underlying position is flagged by reconciliation (not clean).
  - consecutive_losses / preservation state survive a simulated restart (EmotionGuard rehydrate).

Run with: pytest tests/test_batch3_risk.py -v
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# These tests exercise capital-preservation / sizing, not market hours or signal
# quality. Force the market open so _execute_signal reaches those stages regardless
# of when the suite runs.
@pytest.fixture(autouse=True)
def _market_always_open():
    with patch("app.utils.market_hours.is_market_open", return_value=True):
        yield


# ══════════════════════════════════════════════════════════════════════════════
# 1. Sizing returns 0 → no trade (risk_manager + paper_trader)
# ══════════════════════════════════════════════════════════════════════════════

def test_risk_manager_returns_zero_when_budget_exhausted():
    """calculate_position_size must return 0, not 1, when budget can't cover one contract."""
    from app.services.risk_manager import RiskManager
    rm = RiskManager()
    result = rm.calculate_position_size(
        portfolio_value=500.0,
        max_loss_per_spread=10_000.0,
        risk_pct=0.02,
    )
    assert result == 0, f"Expected 0 contracts, got {result}"


def test_sizing_floor_removed_from_risk_manager():
    """Verify the old `max(contracts, 1)` floor is gone from calculate_position_size."""
    import inspect
    from app.services.risk_manager import RiskManager
    src = inspect.getsource(RiskManager.calculate_position_size)
    assert "max(contracts, 1)" not in src, "Sizing floor max(contracts, 1) must be removed"
    assert "max(contracts, 0)" in src, "Floor must be max(contracts, 0)"


# ══════════════════════════════════════════════════════════════════════════════
# 2. Capital preservation: disallowed strategies blocked; allowed pass
# ══════════════════════════════════════════════════════════════════════════════

def _preservation_portfolio():
    """PortfolioState at 80% of capital — triggers capital_preservation_threshold (default 0.85)."""
    from app.services.guardrails import PortfolioState
    from app.core.config import settings
    capital = settings.starting_capital
    return PortfolioState(
        current_value=capital * 0.80,
        starting_capital=capital,
        daily_pnl=Decimal("0"),
        weekly_pnl=Decimal("0"),
        monthly_pnl=Decimal("0"),
        consecutive_losses=0,
        trades_today=0,
    )


def test_capital_preservation_blocks_iron_condor():
    from app.services.guardrails import GuardrailEngine
    engine = GuardrailEngine()
    status = engine.check_all(_preservation_portfolio())
    assert status.trading_mode == "capital_preservation"
    assert not engine.is_strategy_allowed("iron_condor", status)


def test_capital_preservation_allows_credit_spreads():
    from app.services.guardrails import GuardrailEngine
    engine = GuardrailEngine()
    status = engine.check_all(_preservation_portfolio())
    assert engine.is_strategy_allowed("bull_put_spread", status)
    assert engine.is_strategy_allowed("bear_call_spread", status)


def test_capital_preservation_halves_position_size():
    from app.services.guardrails import GuardrailEngine
    engine = GuardrailEngine()
    status = engine.check_all(_preservation_portfolio())
    assert engine.get_position_size_multiplier(status) == 0.50


@pytest.mark.asyncio
async def test_execute_signal_blocks_iron_condor_in_preservation():
    """_execute_signal must block a disallowed strategy in capital_preservation mode."""
    from app.api.routes.trade_desk import _execute_signal

    signal = {
        "id": "sig-ic-001",
        "ticker": "SPY",
        "action": "SELL",
        "asset_type": "options",
        "strategy": "iron_condor",
        "quantity": 1,
        "confidence": 0.92,
        "spread": {
            "expiration": "2025-06-20",
            "short_strike": 450, "long_strike": 440,
            "option_type": "put", "net_credit": 2.00, "max_loss": 8.00,
        },
    }

    with patch(
        "app.api.routes.trade_desk._fetch_portfolio_state",
        new=AsyncMock(return_value=_preservation_portfolio()),
    ), patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False):

        result = await _execute_signal(signal, approved_by="autopilot")

    assert result["result"] == "blocked"
    assert "capital_preservation" in result["reason"]


@pytest.mark.asyncio
async def test_execute_signal_allows_credit_spread_in_preservation(stub_position_claim):
    """bull_put_spread must be allowed in capital_preservation mode."""
    from app.api.routes.trade_desk import _execute_signal

    signal = {
        "id": "sig-bps-001",
        "ticker": "SPY",
        "action": "SELL",
        "asset_type": "options",
        "strategy": "bull_put_spread",
        "quantity": 1,
        "confidence": 0.92,
        "spread": {
            "expiration": "2025-06-20",
            "short_strike": 450, "long_strike": 440,
            "option_type": "put", "net_credit": 1.50, "max_loss": 3.50,
        },
    }

    broker = MagicMock()
    broker.place_order = AsyncMock(return_value=MagicMock(order_id="ORD-1", status="submitted"))

    recorder = MagicMock()
    recorder.record_fill = AsyncMock(return_value="trade-uuid")

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(return_value=MagicMock(first=lambda: None))

    with patch(
        "app.api.routes.trade_desk._fetch_portfolio_state",
        new=AsyncMock(return_value=_preservation_portfolio()),
    ), patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
       patch("app.broker.broker_factory.get_broker", return_value=broker), \
       patch("app.services.account_guard.verify_account_mode",
             new=AsyncMock(return_value=(True, "account DU-test (paper)"))), \
       patch("app.services.trade_recorder.trade_recorder", recorder), \
       patch("app.core.database.AsyncSessionLocal", return_value=mock_session):

        result = await _execute_signal(signal, approved_by="autopilot")

    assert result["result"] == "submitted"


# ══════════════════════════════════════════════════════════════════════════════
# 3. Scalper mode risk_per_trade_pct reaches position sizing
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_active_risk_pct_reads_from_trading_mode():
    """_active_risk_pct() must return the active mode's risk_per_trade_pct, not 0.02."""
    from app.services.strategy_engine import _active_risk_pct
    from app.services.trading_mode import trading_mode_manager, TradingModeType

    # Set to scalper mode (risk_per_trade_pct = 0.0075 — 2026-07-28 rebalance)
    await trading_mode_manager.set_mode(TradingModeType.SCALPER)
    pct = _active_risk_pct()
    assert pct == 0.0075, f"Expected scalper risk 0.0075, got {pct}"

    # Reset to balanced (risk_per_trade_pct = 0.01 since the 2026-07-28
    # rebalance — Balanced now runs the old Conservative profile)
    await trading_mode_manager.set_mode(TradingModeType.BALANCED)
    pct = _active_risk_pct()
    assert pct == 0.01, f"Expected balanced risk 0.01, got {pct}"


def test_size_position_uses_active_mode_risk():
    """BullPutSpread.size_position must use _active_risk_pct(), not hardcoded 0.02."""
    import inspect
    from app.services.strategy_engine import BullPutSpread
    src = inspect.getsource(BullPutSpread.size_position)
    assert "risk_pct=0.02" not in src, "BullPutSpread.size_position must not hardcode risk_pct=0.02"
    assert "_active_risk_pct()" in src


# ══════════════════════════════════════════════════════════════════════════════
# 4. Doubled same-underlying position flagged by reconciliation
# ══════════════════════════════════════════════════════════════════════════════

def _mock_position(underlying: str, qty: int):
    """MagicMock that looks like a broker Position with underlying + quantity."""
    p = MagicMock()
    p.symbol = underlying
    p.underlying = underlying
    p.quantity = qty
    return p


def _mock_db_trade(underlying: str, qty: int):
    t = MagicMock()
    t.underlying = underlying
    t.quantity = qty
    t.status = "open"
    return t


def _mock_session_with_trades(trades: list):
    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(
        return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: trades))
    )
    return mock_session


@pytest.mark.asyncio
async def test_reconciliation_flags_doubled_position():
    """
    Broker reports qty=2 for SPY but DB has qty=1 → ReconciliationError (quantity mismatch).
    """
    from app.services.position_reconciler import PositionReconciler, ReconciliationError

    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[_mock_position("SPY", 2)])

    reconciler = PositionReconciler(broker)

    with patch("app.services.position_reconciler.AsyncSessionLocal",
               return_value=_mock_session_with_trades([_mock_db_trade("SPY", 1)])):
        with pytest.raises(ReconciliationError, match="(?i)quantity mismatch"):
            await reconciler.reconcile()


@pytest.mark.asyncio
async def test_reconciliation_clean_when_quantities_match():
    """Broker qty=1, DB qty=1 → clean reconciliation."""
    from app.services.position_reconciler import PositionReconciler

    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[_mock_position("SPY", 1)])

    reconciler = PositionReconciler(broker)

    with patch("app.services.position_reconciler.AsyncSessionLocal",
               return_value=_mock_session_with_trades([_mock_db_trade("SPY", 1)])):
        result = await reconciler.reconcile()

    assert result.clean is True


# ══════════════════════════════════════════════════════════════════════════════
# 5. EmotionGuard state survives simulated restart (rehydrate from DB)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_emotion_guard_rehydrates_consecutive_losses():
    """After a simulated restart, EmotionGuard must restore consecutive_losses from DB."""
    from app.services.unified_risk import EmotionGuard
    from datetime import datetime, timezone

    snap = MagicMock()
    snap.consecutive_losses = 3
    snap.cooling_off_until = None

    guard = EmotionGuard()
    assert guard.state.consecutive_losses == 0  # starts fresh

    with patch("app.core.database.AsyncSessionLocal") as mock_db:
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: snap)
        )
        mock_db.return_value = mock_session

        await guard.rehydrate_from_db()

    assert guard.state.consecutive_losses == 3


@pytest.mark.asyncio
async def test_emotion_guard_rehydrates_paused_until():
    """paused_until must be restored from DB so a pause survives restart."""
    from app.services.unified_risk import EmotionGuard
    from datetime import datetime, timezone

    future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    snap = MagicMock()
    snap.consecutive_losses = 4
    snap.cooling_off_until = future

    guard = EmotionGuard()

    with patch("app.core.database.AsyncSessionLocal") as mock_db:
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=lambda: snap)
        )
        mock_db.return_value = mock_session

        await guard.rehydrate_from_db()

    assert guard.state.paused_until == future
    allowed, reason = guard.check()
    assert not allowed
    assert "pause" in reason.lower()


@pytest.mark.asyncio
async def test_emotion_guard_persists_after_loss():
    """record_trade(was_loss=True) must persist consecutive_losses to DB."""
    from app.services.unified_risk import EmotionGuard

    guard = EmotionGuard()
    persisted: list[dict] = []

    async def _fake_persist():
        persisted.append({"losses": guard.state.consecutive_losses})

    guard._persist_to_db = _fake_persist

    guard.record_trade(was_loss=True, position_size=500, baseline_size=500)
    await guard._persist_to_db()

    assert len(persisted) == 1
    assert persisted[0]["losses"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# 6. Decimal in guardrails — PnL precision
# ══════════════════════════════════════════════════════════════════════════════

def test_portfolio_state_accepts_decimal_pnl():
    """PortfolioState must accept Decimal for PnL fields."""
    from app.services.guardrails import PortfolioState
    state = PortfolioState(
        current_value=100_000.0,
        starting_capital=100_000.0,
        daily_pnl=Decimal("-500.123456789"),
        weekly_pnl=Decimal("0"),
        monthly_pnl=Decimal("0"),
        consecutive_losses=0,
        trades_today=0,
    )
    assert isinstance(state.daily_pnl, Decimal)


def test_guardrail_check_uses_decimal_arithmetic():
    """GuardrailEngine.check_all must not lose precision on Decimal PnL comparisons."""
    from app.services.guardrails import GuardrailEngine, PortfolioState
    from app.core.config import settings

    limit = settings.max_daily_loss_pct
    capital = settings.starting_capital
    # Exactly at the limit in Decimal — float rounding could fail this
    daily_loss = Decimal(str(-capital * limit))

    state = PortfolioState(
        current_value=float(capital),
        starting_capital=float(capital),
        daily_pnl=daily_loss,
        weekly_pnl=Decimal("0"),
        monthly_pnl=Decimal("0"),
        consecutive_losses=0,
        trades_today=0,
    )
    status = GuardrailEngine().check_all(state)
    # At exactly the limit, trading should be blocked
    assert not status.trading_allowed
    assert "daily" in (status.reason or "").lower()
