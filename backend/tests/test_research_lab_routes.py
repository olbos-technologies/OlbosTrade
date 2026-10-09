"""Tests for the Research Lab API routes (DB mocked)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes.research import (
    CreateExperiment, TransitionRequest,
    create_experiment, list_experiments, transition_experiment, lab_baselines,
    get_registry, get_features,
)
from app.services.research_lab import BACKTESTED, DRAFT, PAPER, PROMOTED, WALK_FORWARD


def _session(rows=None, one=None):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    result = MagicMock()
    result.scalars.return_value = MagicMock(all=lambda: rows or [])
    result.scalar_one_or_none = lambda: one
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


class _Exp:
    """Mutable stand-in for a ResearchExperiment row."""
    def __init__(self, **kw):
        self.stage = kw.get("stage", DRAFT)
        self.backtest_metrics = kw.get("backtest_metrics")
        self.paper_perf = kw.get("paper_perf")
        self.baseline = kw.get("baseline")
        self.strategy = kw.get("strategy", "bull_put_spread")
        self._d = kw

    def as_dict(self):
        return {"id": "e1", "name": "exp", "strategy": self.strategy,
                "stage": self.stage, "backtest_metrics": self.backtest_metrics,
                "paper_perf": self.paper_perf, "baseline": self.baseline}


@pytest.mark.asyncio
async def test_list_experiments():
    rows = [_Exp(stage=DRAFT), _Exp(stage=PROMOTED)]
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(rows=rows)):
        out = await list_experiments()
    assert out["total"] == 2 and len(out["experiments"]) == 2


@pytest.mark.asyncio
async def test_list_experiments_db_error():
    with patch("app.core.database.AsyncSessionLocal", side_effect=Exception("down")):
        out = await list_experiments()
    assert out["experiments"] == [] and "error" in out


@pytest.mark.asyncio
async def test_create_experiment():
    with patch("app.core.database.AsyncSessionLocal", return_value=_session()), \
         patch("app.models.research_experiment.ResearchExperiment") as M:
        M.return_value = _Exp(stage=DRAFT)
        out = await create_experiment(CreateExperiment(
            name="Mean revert SPY", strategy="iron_condor", hypothesis="range-bound"))
    assert out["stage"] == DRAFT


@pytest.mark.asyncio
async def test_create_experiment_error():
    with patch("app.core.database.AsyncSessionLocal", side_effect=Exception("boom")):
        out = await create_experiment(CreateExperiment(name="x", strategy="y"))
    assert "error" in out


_PROV = {
    "source": "backtester",
    "run_id": "11111111-1111-4111-8111-111111111111",
    "strategy": "bull_put_spread",
    "start_date": "2024-01-01",
    "end_date": "2024-12-31",
    "parameters": {},
    "recorded_at": "2026-01-01T00:00:00+00:00",
}


def _traced(metrics: dict) -> dict:
    """Evidence shaped the way the route now writes it — numbers plus the run
    they came from. _Exp's strategy defaults to "bull_put_spread", which _PROV matches."""
    return {**metrics, "provenance": _PROV}


@pytest.mark.asyncio
async def test_transition_not_found():
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(one=None)):
        out = await transition_experiment("missing", TransitionRequest(target=BACKTESTED))
    assert out["error"] == "experiment not found"


@pytest.mark.asyncio
async def test_transition_gate_blocks():
    exp = _Exp(stage=BACKTESTED, backtest_metrics=_traced({"sharpe": 0.1, "total_return_pct": 1, "max_drawdown_pct": 5}))
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(one=exp)):
        out = await transition_experiment("e1", TransitionRequest(target=WALK_FORWARD))
    assert out["ok"] is False and "backtest gate failed" in out["reason"]


@pytest.mark.asyncio
async def test_transition_success_persists_patch():
    exp = _Exp(stage=BACKTESTED, backtest_metrics=_traced({"sharpe": 1.5, "total_return_pct": 20, "max_drawdown_pct": 8}))
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(one=exp)):
        out = await transition_experiment("e1", TransitionRequest(target=WALK_FORWARD))
    assert out["ok"] is True and exp.stage == WALK_FORWARD


@pytest.mark.asyncio
async def test_transition_promote_sets_baseline():
    exp = _Exp(stage=PAPER, backtest_metrics=_traced({"max_drawdown_pct": 15.0}))
    perf = {"total_trades": 40, "win_rate": 0.7, "expectancy": 35.0, "max_drawdown_trades_pct": 9.0}
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(one=exp)):
        out = await transition_experiment("e1", TransitionRequest(target=PROMOTED, perf=perf))
    assert out["ok"] is True and exp.stage == PROMOTED
    assert exp.baseline["win_rate"] == 0.7


@pytest.mark.asyncio
async def test_transition_handles_exception():
    with patch("app.core.database.AsyncSessionLocal", side_effect=Exception("kaboom")):
        out = await transition_experiment("e1", TransitionRequest(target=PAPER))
    assert out["ok"] is False and "error" in out["reason"]


@pytest.mark.asyncio
async def test_lab_baselines():
    rows = [_Exp(stage=PROMOTED, strategy="bull_put_spread",
                 baseline={"win_rate": 0.8, "expectancy": 50, "max_drawdown_pct": 20})]
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(rows=rows)):
        out = await lab_baselines()
    assert out["baselines"]["bull_put_spread"]["expectancy"] == 50


@pytest.mark.asyncio
async def test_lab_baselines_db_error():
    with patch("app.core.database.AsyncSessionLocal", side_effect=Exception("x")):
        out = await lab_baselines()
    assert out["baselines"] == {} and "error" in out


# ── registry + features endpoints ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_get_registry():
    rows = [_Exp(stage=PROMOTED, strategy="bull_put_spread"),
            _Exp(stage=DRAFT, strategy="iron_condor")]
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(rows=rows)):
        out = await get_registry()
    assert out["summary"]["total_strategies"] == 2
    names = {s["strategy"] for s in out["strategies"]}
    assert names == {"bull_put_spread", "iron_condor"}
    assert all("metadata_complete" in s for s in out["strategies"])


@pytest.mark.asyncio
async def test_get_registry_db_error():
    with patch("app.core.database.AsyncSessionLocal", side_effect=Exception("down")):
        out = await get_registry()
    assert out["strategies"] == [] and "error" in out


@pytest.mark.asyncio
async def test_get_features_catalog():
    out = await get_features()
    assert out["total"] >= 12
    assert any(f["name"] == "rsi" for f in out["features"])


@pytest.mark.asyncio
async def test_route_refuses_caller_supplied_metrics():
    """The hole this closes: the UI posted sharpe 1.0 and the gates, which
    judge whatever dict they are handed, cleared it. Metrics in a request body
    have no provenance and the route will not forward them."""
    exp = _Exp(stage=DRAFT)
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(one=exp)):
        out = await transition_experiment("e1", TransitionRequest(
            target=BACKTESTED,
            metrics={"sharpe": 1.0, "total_return_pct": 12.0, "max_drawdown_pct": 10.0},
        ))
    assert out["ok"] is False
    assert "may not be supplied by the caller" in out["reason"]
    assert exp.stage == DRAFT, "a refused transition must not move the experiment"


@pytest.mark.asyncio
async def test_route_refuses_caller_supplied_walk_forward_metrics():
    exp = _Exp(stage=WALK_FORWARD)
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(one=exp)):
        out = await transition_experiment("e1", TransitionRequest(
            target=PAPER,
            wf_metrics={"oos_sharpe": 0.9, "oos_return_pct": 9.0, "max_drawdown_pct": 11.0},
        ))
    assert out["ok"] is False
    assert "may not be supplied by the caller" in out["reason"]
    assert exp.stage == WALK_FORWARD


@pytest.mark.asyncio
async def test_route_rejects_an_unknown_backtest_run():
    exp = _Exp(stage=DRAFT)
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(one=None)):
        out = await transition_experiment("e1", TransitionRequest(
            target=BACKTESTED, backtest_run_id="not-a-uuid",
        ))
    assert "error" in out or out.get("ok") is False
