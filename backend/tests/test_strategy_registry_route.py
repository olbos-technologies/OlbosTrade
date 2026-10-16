"""GET /api/strategy/registry — seeded StrategyProfile rows merged with live health."""

from __future__ import annotations

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from conftest import single_operator_conn

# Batch E2 gave these routes a `conn` so they can resolve a trade scope; with
# auth disabled it is never read. See conftest.single_operator_conn.
_CONN = single_operator_conn()



def _profile(**overrides) -> NS:
    base = dict(
        strategy_id="bull_put_spread", name="Bull Put Spread", version="1.0.0",
        asset_class="options", supported_symbols=[], supported_regimes=[],
        supported_volatility_regimes=[], risk_profile_compatibility=[],
        manual_eligible=True, copilot_eligible=True, autopilot_supported=False,
        lifecycle_status="paper", enabled=True, allocation_limit_pct=None,
        main_risk_warning=None,
    )
    base.update(overrides)
    return NS(**base)


def _profiles_session(profiles):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    scalars = MagicMock()
    scalars.scalars.return_value = MagicMock(all=lambda: profiles)
    session.execute = AsyncMock(return_value=scalars)
    return session


def _health(strategy, status="healthy", score=72.5, sample_size=25):
    return NS(strategy=strategy, status=status, score=score, sample_size=sample_size)


@pytest.mark.asyncio
async def test_registry_route_returns_all_seeded_strategies():
    from app.api.routes import strategy as strat_mod
    profiles = [
        _profile(strategy_id="bull_put_spread", name="Bull Put Spread"),
        _profile(strategy_id="bear_call_spread", name="Bear Call Spread"),
        _profile(strategy_id="iron_condor", name="Iron Condor"),
        _profile(strategy_id="bull_call_debit_spread", name="Bull Call Debit Spread"),
    ]
    with patch("app.core.database.AsyncSessionLocal", return_value=_profiles_session(profiles)), \
         patch.object(strat_mod, "_evaluate_health", new=AsyncMock(return_value=[])):
        out = await strat_mod.get_strategy_registry(_CONN)

    assert out["total"] == 4
    ids = {c["strategy_id"] for c in out["strategies"]}
    assert ids == {"bull_put_spread", "bear_call_spread", "iron_condor", "bull_call_debit_spread"}
    # No closed trades for any strategy yet — health fields must not be fabricated.
    for card in out["strategies"]:
        assert card["health_score"] is None
        assert card["health_status"] is None
        assert card["sample_size"] == 0


@pytest.mark.asyncio
async def test_registry_route_merges_live_health_for_matching_strategy_only():
    from app.api.routes import strategy as strat_mod
    profiles = [
        _profile(strategy_id="bull_put_spread"),
        _profile(strategy_id="iron_condor", main_risk_warning="Suspended in capital-preservation mode."),
    ]
    with patch("app.core.database.AsyncSessionLocal", return_value=_profiles_session(profiles)), \
         patch.object(strat_mod, "_evaluate_health",
                      new=AsyncMock(return_value=[_health("bull_put_spread")])):
        out = await strat_mod.get_strategy_registry(_CONN)

    by_id = {c["strategy_id"]: c for c in out["strategies"]}
    assert by_id["bull_put_spread"]["health_score"] == 72.5
    assert by_id["bull_put_spread"]["health_status"] == "healthy"
    assert by_id["bull_put_spread"]["sample_size"] == 25
    # iron_condor has no matching health row — must stay explicitly unknown, not 0/healthy.
    assert by_id["iron_condor"]["health_score"] is None
    assert by_id["iron_condor"]["health_status"] is None
    assert by_id["iron_condor"]["main_risk_warning"] == "Suspended in capital-preservation mode."


@pytest.mark.asyncio
async def test_registry_route_survives_health_evaluation_error():
    from app.api.routes import strategy as strat_mod
    profiles = [_profile()]
    with patch("app.core.database.AsyncSessionLocal", return_value=_profiles_session(profiles)), \
         patch.object(strat_mod, "_evaluate_health", new=AsyncMock(side_effect=Exception("db down"))):
        out = await strat_mod.get_strategy_registry(_CONN)
    assert out["total"] == 1
    assert out["strategies"][0]["health_score"] is None


@pytest.mark.asyncio
async def test_registry_single_route_returns_matching_card():
    from app.api.routes import strategy as strat_mod
    profiles = [_profile(strategy_id="bull_put_spread"), _profile(strategy_id="iron_condor")]
    with patch("app.core.database.AsyncSessionLocal", return_value=_profiles_session(profiles)), \
         patch.object(strat_mod, "_evaluate_health", new=AsyncMock(return_value=[])):
        card = await strat_mod.get_strategy_registry_one("iron_condor", _CONN)
    assert card["strategy_id"] == "iron_condor"


@pytest.mark.asyncio
async def test_registry_single_route_404s_for_unknown_strategy():
    from fastapi import HTTPException
    from app.api.routes import strategy as strat_mod
    profiles = [_profile(strategy_id="bull_put_spread")]
    with patch("app.core.database.AsyncSessionLocal", return_value=_profiles_session(profiles)), \
         patch.object(strat_mod, "_evaluate_health", new=AsyncMock(return_value=[])):
        with pytest.raises(HTTPException) as exc_info:
            await strat_mod.get_strategy_registry_one("not_a_real_strategy", _CONN)
    assert exc_info.value.status_code == 404
