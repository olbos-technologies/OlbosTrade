"""Strategy Health Monitor — execution-gate (Stage 1d), DB helper, and API route."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.guardrails import PortfolioState
from app.services.strategy_health import StrategyHealth, DEGRADED, HEALTHY

from conftest import single_operator_conn

# Batch E2 gave these routes a `conn` so they can resolve a trade scope; with
# auth disabled it is never read. See conftest.single_operator_conn.
_CONN = single_operator_conn()



@pytest.fixture(autouse=True)
def _market_always_open():
    with patch("app.utils.market_hours.is_market_open", return_value=True):
        yield


def _options_signal():
    return {
        "id": "sig-h1", "ticker": "SPY", "action": "SELL", "asset_type": "options",
        "strategy": "bull_put_spread", "quantity": 1, "confidence": 0.92, "pop": 0.82,
        "spread": {"expiration": "2025-06-20", "short_strike": 450, "long_strike": 445,
                   "option_type": "put", "net_credit": 1.50, "max_loss": 3.50},
        "signal_score": 0.82, "iv_rank": 45.0, "regime": "normal_mean_revert",
    }


def _clean_portfolio():
    return PortfolioState(current_value=100_000.0, starting_capital=100_000.0,
                          daily_pnl=0.0, weekly_pnl=0.0, monthly_pnl=0.0,
                          consecutive_losses=0, trades_today=0)


def _patch_fetch():
    return patch("app.api.routes.trade_desk._fetch_portfolio_state",
                 new=AsyncMock(return_value=_clean_portfolio()))


def _degraded():
    return StrategyHealth("bull_put_spread", DEGRADED, "suspend", 25, 0.4, -30.0,
                          0.6, 18.0, 7, 0.2, ["negative live expectancy (-30.00)"])


def _healthy():
    return StrategyHealth("bull_put_spread", HEALTHY, "none", 25, 0.82, 60.0,
                          2.1, 3.0, 1, 1.2, ["performing at or above baseline"])


# ── Stage 1d gate ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_degraded_strategy_blocks_entry():
    from app.api.routes.trade_desk import _execute_signal
    with _patch_fetch(), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.api.routes.trade_desk._strategy_health_for",
               new=AsyncMock(return_value=_degraded())):
        res = await _execute_signal(_options_signal(), approved_by="autopilot")
    assert res["result"] == "blocked"
    assert "strategy_health" in res["reason"]


@pytest.mark.asyncio
async def test_healthy_strategy_passes_health_gate():
    """Healthy → not blocked by the health gate (it proceeds to later stages)."""
    from app.api.routes.trade_desk import _execute_signal
    with _patch_fetch(), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.api.routes.trade_desk._strategy_health_for",
               new=AsyncMock(return_value=_healthy())), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()):
        res = await _execute_signal(_options_signal(), approved_by="autopilot")
    assert "strategy_health" not in res.get("reason", "")


@pytest.mark.asyncio
async def test_manual_trade_bypasses_health_gate():
    """Manual override must skip the health gate entirely (never calls it)."""
    from app.api.routes.trade_desk import _execute_signal
    spy = AsyncMock(return_value=_degraded())
    with _patch_fetch(), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.api.routes.trade_desk._strategy_health_for", new=spy), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()):
        res = await _execute_signal(_options_signal(), approved_by="manual")
    spy.assert_not_called()
    assert "strategy_health" not in res.get("reason", "")


@pytest.mark.asyncio
async def test_gate_fails_open_when_health_none():
    from app.api.routes.trade_desk import _execute_signal
    with _patch_fetch(), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.api.routes.trade_desk._strategy_health_for",
               new=AsyncMock(return_value=None)), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()):
        res = await _execute_signal(_options_signal(), approved_by="autopilot")
    assert "strategy_health" not in res.get("reason", "")


# ── _strategy_health_for DB helper ───────────────────────────────────────────────
def _db_session(trades):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    scalars = MagicMock()
    scalars.scalars.return_value = MagicMock(all=lambda: trades)
    session.execute = AsyncMock(return_value=scalars)
    return session


@pytest.mark.asyncio
async def test_strategy_health_for_reads_db():
    from app.api.routes.trade_desk import _strategy_health_for
    rows = [MagicMock(pnl=50.0, entry_date=date(2024, 1, 1), exit_date=date(2024, 1, 2))
            for _ in range(25)]
    with patch("app.core.database.AsyncSessionLocal", return_value=_db_session(rows)):
        h = await _strategy_health_for("bull_put_spread")
    assert h is not None and h.strategy == "bull_put_spread" and h.sample_size == 25


@pytest.mark.asyncio
async def test_strategy_health_for_returns_none_on_error():
    from app.api.routes.trade_desk import _strategy_health_for
    with patch("app.core.database.AsyncSessionLocal", side_effect=Exception("db down")):
        h = await _strategy_health_for("bull_put_spread")
    assert h is None


# ── /api/strategy/health route ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_route_returns_graded_strategies():
    from app.api.routes.strategy import get_strategy_health
    rows = ([MagicMock(strategy="bull_put_spread", pnl=60.0,
                       entry_date=date(2024, 1, 1), exit_date=date(2024, 1, 2))
             for _ in range(20)]
            + [MagicMock(strategy="bull_put_spread", pnl=-40.0,
                         entry_date=date(2024, 1, 3), exit_date=date(2024, 1, 4))
               for _ in range(5)])
    with patch("app.core.database.AsyncSessionLocal", return_value=_db_session(rows)):
        out = await get_strategy_health(_CONN, min_sample=20)
    assert out["total_strategies"] == 1
    assert out["strategies"][0]["strategy"] == "bull_put_spread"
    assert "suspended" in out


@pytest.mark.asyncio
async def test_route_handles_db_error():
    from app.api.routes.strategy import get_strategy_health
    with patch("app.core.database.AsyncSessionLocal", side_effect=Exception("boom")):
        out = await get_strategy_health(_CONN)
    assert out["strategies"] == [] and "error" in out


# ── /api/strategy/meta route ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_meta_route_returns_decisions():
    from app.api.routes import strategy as strat_mod
    health = [_healthy()]
    import app.main as m
    m._current_regime = NS(regime=NS(value="normal_mean_revert"))
    with patch.object(strat_mod, "_evaluate_health", new=AsyncMock(return_value=health)):
        out = await strat_mod.get_meta_strategy(_CONN, min_sample=20)
    assert out["regime"] == "normal_mean_revert"
    assert "bull_put_spread" in out["active_strategies"]
    assert out["decisions"][0]["strategy"] == "bull_put_spread"


@pytest.mark.asyncio
async def test_meta_route_handles_error():
    from app.api.routes import strategy as strat_mod
    with patch.object(strat_mod, "_evaluate_health",
                      new=AsyncMock(side_effect=Exception("down"))):
        out = await strat_mod.get_meta_strategy(_CONN)
    assert out["decisions"] == [] and "error" in out
