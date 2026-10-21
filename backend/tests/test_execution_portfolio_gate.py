"""Step 8 — execution portfolio gate (pure + OMS wiring)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.execution_portfolio_gate import (
    REGIME_CONCURRENCY_MULTIPLIER,
    PortfolioGateResult,
    estimate_proposed_risk_dollars,
    evaluate_portfolio_gates,
)
from app.services.risk_manager import PortfolioRiskState, RiskManager


def _portfolio(
    *,
    value: float = 100_000.0,
    open_count: int = 0,
    by_u: dict | None = None,
    by_s: dict | None = None,
) -> PortfolioRiskState:
    return PortfolioRiskState(
        net_delta=0.0,
        net_vega=0.0,
        net_theta=0.0,
        open_position_count=open_count,
        portfolio_value=value,
        positions_by_underlying=by_u or {},
        positions_by_sector=by_s or {},
    )


def _equity(ticker="AAPL", shares=10, entry=150.0, stop=145.0):
    return {
        "ticker": ticker,
        "asset_type": "equity",
        "trade_plan": {"shares": shares, "entry_price": entry, "stop_price": stop},
    }


def _options(ticker="SPY", qty=1, max_loss=350.0):
    return {
        "ticker": ticker,
        "asset_type": "options",
        "strategy": "bull_put_spread",
        "quantity": qty,
        "spread": {"max_loss": max_loss},
    }


def test_estimate_equity_risk_uses_stop_distance():
    assert estimate_proposed_risk_dollars(_equity()) == 50.0  # 5 * 10


def test_estimate_options_risk_uses_max_loss_times_qty():
    assert estimate_proposed_risk_dollars(_options(qty=2, max_loss=350.0)) == 700.0


def test_blocks_max_positions():
    r = evaluate_portfolio_gates(
        _equity(),
        _portfolio(open_count=RiskManager().max_concurrent),
    )
    assert not r.allowed
    assert "max_positions" in r.flags


@pytest.mark.asyncio
async def test_scalper_mode_tightens_max_concurrent_to_its_own_cap():
    """Scalper's own config caps concurrent positions at 3 (compounding gamma
    risk on 0-3 DTE) — previously only the global default (5) was enforced
    here, silently allowing more concurrent positions than Scalper's own
    design intends. Compared against Aggressive (max_concurrent=6, since the
    2026-07-28 rebalance) rather than Balanced — Balanced now shares
    Scalper's same 3-position cap, so it wouldn't show the mode-specific
    distinction this test exists to prove."""
    from app.services.trading_mode import trading_mode_manager, TradingModeType

    await trading_mode_manager.set_mode(TradingModeType.SCALPER)
    try:
        # 3 open positions: global cap (5) would allow this, Scalper's own
        # cap (3) must not.
        r = evaluate_portfolio_gates(_equity(), _portfolio(open_count=3))
        assert not r.allowed
        assert "max_positions" in r.flags
    finally:
        await trading_mode_manager.set_mode(TradingModeType.AGGRESSIVE)

    # In Aggressive (max_concurrent=6): the same 3 open positions must not
    # be blocked — confirms the tightening is mode-scoped, not a blanket cut.
    r = evaluate_portfolio_gates(_equity(), _portfolio(open_count=3))
    assert r.allowed
    await trading_mode_manager.set_mode(TradingModeType.BALANCED)


def _with_regime(regime):
    sig = _equity()
    sig["regime"] = regime
    return sig


def test_regime_multiplier_table_values_do_not_exceed_one():
    """Tighten-only contract: regime must never be able to raise capacity
    above the mode/global caps already computed above it."""
    for value in REGIME_CONCURRENCY_MULTIPLIER.values():
        assert value <= 1.0


@pytest.mark.asyncio
async def test_low_vol_and_normal_regime_do_not_tighten_max_concurrent():
    from app.services.trading_mode import trading_mode_manager, TradingModeType

    await trading_mode_manager.set_mode(TradingModeType.BALANCED)
    try:
        for regime in ("low_vol_trending", "normal_mean_revert"):
            r = evaluate_portfolio_gates(_with_regime(regime), _portfolio(open_count=3))
            assert not r.allowed  # identical to the pre-increment baseline at mode cap
            assert "max_positions" in r.flags
            assert "regime_tightened_capacity" not in r.flags

            r = evaluate_portfolio_gates(_with_regime(regime), _portfolio(open_count=2))
            assert r.allowed
    finally:
        await trading_mode_manager.set_mode(TradingModeType.BALANCED)


@pytest.mark.asyncio
async def test_high_vol_trending_regime_tightens_max_concurrent():
    from app.services.trading_mode import trading_mode_manager, TradingModeType

    await trading_mode_manager.set_mode(TradingModeType.BALANCED)
    try:
        # Balanced max_concurrent=3 -> floor(3*0.75)=2
        r = evaluate_portfolio_gates(_with_regime("high_vol_trending"), _portfolio(open_count=2))
        assert not r.allowed
        assert "regime_tightened_capacity" in r.flags
        assert "regime-tightened" in r.reason

        r = evaluate_portfolio_gates(_with_regime("high_vol_trending"), _portfolio(open_count=1))
        assert r.allowed
    finally:
        await trading_mode_manager.set_mode(TradingModeType.BALANCED)


@pytest.mark.asyncio
async def test_unknown_regime_tightens_max_concurrent():
    from app.services.trading_mode import trading_mode_manager, TradingModeType

    await trading_mode_manager.set_mode(TradingModeType.BALANCED)
    try:
        # Balanced max_concurrent=3 -> floor(3*0.5)=1
        r = evaluate_portfolio_gates(_with_regime("unknown"), _portfolio(open_count=1))
        assert not r.allowed
        r = evaluate_portfolio_gates(_with_regime("unknown"), _portfolio(open_count=0))
        assert r.allowed
    finally:
        await trading_mode_manager.set_mode(TradingModeType.BALANCED)


@pytest.mark.asyncio
async def test_crisis_regime_floors_max_concurrent_to_one():
    """0.0 multiplier would compute floor(3*0.0)=0, but the gate must never
    fully deadlock via this lever alone — floored to 1. CRISIS's real block
    lives upstream (signal generation already skips CRISIS entirely); this
    is defense-in-depth against a hand-built signal, e.g. manual trade."""
    from app.services.trading_mode import trading_mode_manager, TradingModeType

    await trading_mode_manager.set_mode(TradingModeType.BALANCED)
    try:
        r = evaluate_portfolio_gates(_with_regime("crisis"), _portfolio(open_count=1))
        assert not r.allowed
        r = evaluate_portfolio_gates(_with_regime("crisis"), _portfolio(open_count=0))
        assert r.allowed
    finally:
        await trading_mode_manager.set_mode(TradingModeType.BALANCED)


@pytest.mark.asyncio
async def test_missing_regime_key_is_noop():
    from app.services.trading_mode import trading_mode_manager, TradingModeType

    await trading_mode_manager.set_mode(TradingModeType.BALANCED)
    try:
        r = evaluate_portfolio_gates(_equity(), _portfolio(open_count=3))  # no "regime" key
        assert not r.allowed
        assert "regime_tightened_capacity" not in r.flags
    finally:
        await trading_mode_manager.set_mode(TradingModeType.BALANCED)


@pytest.mark.asyncio
async def test_unrecognized_regime_string_is_noop():
    """A future RegimeType added without updating this table must fail
    toward current behavior, never toward maximum restriction."""
    from app.services.trading_mode import trading_mode_manager, TradingModeType

    await trading_mode_manager.set_mode(TradingModeType.BALANCED)
    try:
        r = evaluate_portfolio_gates(_with_regime("some_future_regime"), _portfolio(open_count=3))
        assert not r.allowed
        assert "regime_tightened_capacity" not in r.flags
        r = evaluate_portfolio_gates(_with_regime("some_future_regime"), _portfolio(open_count=2))
        assert r.allowed
    finally:
        await trading_mode_manager.set_mode(TradingModeType.BALANCED)


@pytest.mark.asyncio
async def test_regime_tightening_composes_with_mode_tightening():
    """Regime must apply to the already mode-tightened max_pos, not the raw
    global cap — Scalper's own 3-cap x 0.75 = 2, not the global 5 x 0.75 = 3."""
    from app.services.trading_mode import trading_mode_manager, TradingModeType

    await trading_mode_manager.set_mode(TradingModeType.SCALPER)
    try:
        r = evaluate_portfolio_gates(_with_regime("high_vol_trending"), _portfolio(open_count=2))
        assert not r.allowed
    finally:
        await trading_mode_manager.set_mode(TradingModeType.BALANCED)


def test_blocks_underlying_concentration():
    # Already 24k in AAPL; adding $50 keeps under 25% — use large risk instead
    sig = _equity(shares=200, entry=200.0, stop=100.0)  # risk = 20_000
    r = evaluate_portfolio_gates(
        sig,
        _portfolio(by_u={"AAPL": 10_000.0}),  # 10k + 20k = 30% > 25%
    )
    assert not r.allowed
    assert "concentration_limit" in r.flags


def test_blocks_sector_concentration_hard():
    # Technology already at 35k; add 10k AAPL risk → 45% > 40%
    sig = _equity(shares=100, entry=200.0, stop=100.0)  # 10_000
    r = evaluate_portfolio_gates(
        sig,
        _portfolio(by_s={"Technology": 35_000.0}, by_u={"MSFT": 35_000.0}),
    )
    assert not r.allowed
    assert "sector_concentration_limit" in r.flags


def test_blocks_projected_heat_high():
    # Diversified underlyings so sector/underlying caps don't fire first.
    # Open risk 45k across Index + new 10k equity in Bonds bucket → heat 55%.
    sig = {
        "ticker": "TLT",
        "asset_type": "equity",
        "trade_plan": {"shares": 100, "entry_price": 200.0, "stop_price": 100.0},
    }
    r = evaluate_portfolio_gates(
        sig,
        _portfolio(by_u={"SPY": 45_000.0}, by_s={"Index": 45_000.0}),
    )
    assert not r.allowed
    assert "portfolio_heat_high" in r.flags


def test_allows_clean_trade():
    r = evaluate_portfolio_gates(_equity(), _portfolio())
    assert r.allowed
    assert r.proposed_risk_dollars == 50.0


def test_greeks_off_by_default_even_if_delta_huge():
    sig = _options()
    sig["delta"] = 1.0
    r = evaluate_portfolio_gates(sig, _portfolio(), enforce_greeks=False)
    assert r.allowed


def test_greeks_block_when_enforced():
    sig = _options()
    sig["delta"] = 0.25
    r = evaluate_portfolio_gates(
        sig,
        PortfolioRiskState(
            net_delta=0.20,
            net_vega=0.0,
            net_theta=0.0,
            open_position_count=0,
            portfolio_value=100_000.0,
            positions_by_underlying={},
            positions_by_sector={},
        ),
        enforce_greeks=True,
    )
    assert not r.allowed
    assert "delta_limit" in r.flags


@pytest.mark.asyncio
async def test_execute_signal_blocks_on_portfolio_gate():
    try:
        from app.api.routes.trade_desk import _execute_signal
    except Exception as exc:
        pytest.skip(f"trade_desk import unavailable on this Python: {exc}")

    from app.services.guardrails import PortfolioState

    clean = PortfolioState(
        current_value=100_000.0,
        starting_capital=100_000.0,
        daily_pnl=0.0,
        weekly_pnl=0.0,
        monthly_pnl=0.0,
        consecutive_losses=0,
        trades_today=0,
    )
    broker = MagicMock()
    broker.place_equity_order = AsyncMock()

    blocked = PortfolioGateResult(
        allowed=False,
        reason="Single underlying concentration: AAPL would be 40.0% of portfolio (max 25%)",
        flags=["concentration_limit"],
        proposed_risk_dollars=5000.0,
    )

    with patch("app.utils.market_hours.is_market_open", return_value=True), \
         patch("app.api.routes.trade_desk._fetch_portfolio_state", new=AsyncMock(return_value=clean)), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch(
             "app.services.execution_portfolio_gate.check_execution_portfolio",
             new=AsyncMock(return_value=blocked),
         ), \
         patch("app.broker.broker_factory.get_broker", return_value=broker):
        result = await _execute_signal(
            {
                "id": "s1",
                "ticker": "AAPL",
                "action": "BUY",
                "asset_type": "equity",
                "confidence": 0.95,
                "trade_plan": {
                    "shares": 10,
                    "entry_price": 150.0,
                    "stop_price": 145.0,
                    "risk_reward": 2.0,
                },
                "indicators": {"volume_ratio": 1.5},
                "signal_score": 0.8,
            },
            approved_by="autopilot",
        )

    assert result["result"] == "blocked"
    assert "portfolio_gate" in result["reason"]
    broker.place_equity_order.assert_not_called()


@pytest.mark.asyncio
async def test_check_disabled_via_settings(monkeypatch):
    from app.core.config import settings
    from app.services.execution_portfolio_gate import check_execution_portfolio

    monkeypatch.setattr(settings, "execution_portfolio_gate", False)
    r = await check_execution_portfolio(_equity(), 100_000.0)
    assert r.allowed
    assert "portfolio_gate_disabled" in r.flags


# ── load_portfolio_risk_state: live broker-count cross-check ──────────────
# Regression coverage for the 2026-08-26 blind-spot fix: DB-tracked open
# count alone can undercount whenever a position exists at the broker with
# no matching Trade row (see execution_portfolio_gate.py's docstring).

def _db_trade(underlying, risk_dollars=1000.0):
    from types import SimpleNamespace as NS
    # Shape position_risk_dollars() reads for an equity row.
    return NS(underlying=underlying, quantity=1, spread_type="equity",
              strategy="equity", credit_received=risk_dollars,
              short_strike=0, long_strike=0)


def _db_session(trades):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    result = MagicMock()
    result.scalars.return_value = MagicMock(all=lambda: trades)
    session.execute = AsyncMock(return_value=result)
    return session


def _broker_position(underlying, quantity=10):
    from types import SimpleNamespace as NS
    return NS(underlying=underlying, quantity=quantity)


@pytest.mark.asyncio
async def test_load_portfolio_risk_state_uses_broker_count_when_higher():
    from app.services.execution_portfolio_gate import load_portfolio_risk_state

    db_trades = [_db_trade("AAPL"), _db_trade("MSFT")]
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[
        _broker_position("AAPL"), _broker_position("MSFT"),
        _broker_position("TSLA"), _broker_position("NVDA"),
    ])

    with patch("app.core.database.AsyncSessionLocal", return_value=_db_session(db_trades)), \
         patch("app.broker.broker_factory.get_broker", return_value=broker):
        state = await load_portfolio_risk_state(100_000.0)

    assert state.open_position_count == 4  # broker's distinct-underlying count wins


@pytest.mark.asyncio
async def test_load_portfolio_risk_state_keeps_db_count_when_broker_not_higher():
    from app.services.execution_portfolio_gate import load_portfolio_risk_state

    db_trades = [_db_trade("AAPL"), _db_trade("MSFT"), _db_trade("SPY")]
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[_broker_position("AAPL")])

    with patch("app.core.database.AsyncSessionLocal", return_value=_db_session(db_trades)), \
         patch("app.broker.broker_factory.get_broker", return_value=broker):
        state = await load_portfolio_risk_state(100_000.0)

    assert state.open_position_count == 3  # DB count is not lowered by a smaller broker count


@pytest.mark.asyncio
async def test_load_portfolio_risk_state_ignores_flat_broker_positions():
    from app.services.execution_portfolio_gate import load_portfolio_risk_state

    db_trades = [_db_trade("AAPL")]
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[
        _broker_position("AAPL"), _broker_position("MSFT", quantity=0),
    ])

    with patch("app.core.database.AsyncSessionLocal", return_value=_db_session(db_trades)), \
         patch("app.broker.broker_factory.get_broker", return_value=broker):
        state = await load_portfolio_risk_state(100_000.0)

    assert state.open_position_count == 1  # zero-quantity broker row excluded from the count


@pytest.mark.asyncio
async def test_load_portfolio_risk_state_broker_failure_falls_back_to_db_count():
    from app.services.execution_portfolio_gate import load_portfolio_risk_state

    db_trades = [_db_trade("AAPL"), _db_trade("MSFT")]
    broker = MagicMock()
    broker.get_positions = AsyncMock(side_effect=Exception("ibkr unavailable"))

    with patch("app.core.database.AsyncSessionLocal", return_value=_db_session(db_trades)), \
         patch("app.broker.broker_factory.get_broker", return_value=broker):
        state = await load_portfolio_risk_state(100_000.0)

    assert state.open_position_count == 2  # falls back to DB-only count, does not raise


@pytest.mark.asyncio
async def test_load_portfolio_risk_state_db_failure_refuses_unknown_exposure():
    from app.services.execution_portfolio_gate import (
        PortfolioStateUnavailable, load_portfolio_risk_state,
    )

    with patch("app.core.database.AsyncSessionLocal", side_effect=Exception("db down")):
        with pytest.raises(PortfolioStateUnavailable, match="portfolio positions unavailable"):
            await load_portfolio_risk_state(100_000.0)


@pytest.mark.asyncio
async def test_check_execution_portfolio_blocks_new_entries_on_db_failure(monkeypatch):
    from app.core.config import settings
    from app.services.execution_portfolio_gate import check_execution_portfolio

    monkeypatch.setattr(settings, "execution_portfolio_gate", True)
    with patch("app.core.database.AsyncSessionLocal", side_effect=Exception("db down")):
        result = await check_execution_portfolio(_equity(), 100_000.0)

    assert result.allowed is False
    assert "portfolio_state_unavailable" in result.flags
    assert "new entries blocked" in result.reason


# ── Unknown is not a sector ─────────────────────────────────────────────
# GILD is unclassified. Before 2026-08-29 it pooled with every other
# unclassified name into one "Unknown" bucket that was then capped at 40%,
# blocking entries with reasons like "Sector concentration: Unknown (GILD)
# would be 94.1%". Those names have nothing in common but the absence of a
# label, and the gate fired on effectively every entry.

def test_unclassified_sector_never_blocks_on_concentration():
    portfolio = _portfolio(value=100_000.0, by_s={"Unknown": 90_000.0})
    r = evaluate_portfolio_gates(_equity(ticker="GILD"), portfolio)
    assert "sector_concentration_limit" not in r.flags
    if not r.allowed:
        assert "Sector concentration" not in (r.reason or "")


def test_a_classified_sector_still_blocks_on_concentration():
    # AAPL resolves to Technology via the static map even with a cold cache.
    portfolio = _portfolio(value=100_000.0, by_s={"Technology": 90_000.0})
    r = evaluate_portfolio_gates(_equity(ticker="AAPL"), portfolio)
    assert r.allowed is False
    assert "sector_concentration_limit" in r.flags
    assert "Technology" in (r.reason or "")
