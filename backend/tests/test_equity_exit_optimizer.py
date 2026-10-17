"""
Tests for the equity exit-geometry sweep.

Every test drives a fake backtester. That is not only for speed: the real
Backtester imports data_fetcher -> `ta`, which does not build in the cloud
container (CLAUDE.md), so a test touching it could not even be collected
there. equity_exit_optimizer keeps its Backtester import behind
TYPE_CHECKING precisely so this file runs everywhere, and
test_module_does_not_import_backtester_at_runtime guards that.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any, Optional

import pytest

from app.services.equity_exit_optimizer import (
    EquityExitOptimizer,
    ExitGeometry,
    GridPoint,
    MIN_TRADES_TO_SCORE,
    UNSCORABLE,
    _MemoFetcher,
    expand_grid,
    resolved_trades,
    score_pooled,
    unresolved_count,
)
from app.services.equity_signal_engine import EquitySignalParams


# ── Fakes ──────────────────────────────────────────────────────────────────

@dataclass
class FakeTrade:
    pnl: float = 0.0
    hold_days: int = 5
    exit_reason: str = "target"
    commission_paid: float = 1.0


class FakeResult:
    def __init__(self, trades: list[FakeTrade]) -> None:
        self.trades = trades


class FakeFetcher:
    """Counts fetches so memoization is observable."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.sentinel = "inner-attr"

    async def fetch_ohlcv(self, ticker: str, start: str, end: str) -> str:
        self.calls.append((ticker, start, end))
        return f"df:{ticker}:{start}:{end}"


class FakeBacktester:
    """Returns trades decided by a caller-supplied plan.

    plan(geometry, ticker, start, end) -> list[FakeTrade] | Exception
    """

    def __init__(self, plan) -> None:
        self.plan = plan
        self.fetcher = FakeFetcher()
        self.runs: list[tuple] = []

    async def run_equity(self, ticker, start, end, starting_capital=25000.0,
                         equity_params=None) -> FakeResult:
        geom = ExitGeometry(
            stop_atr_multiplier=equity_params.stop_atr_multiplier,
            target_atr_multiplier=equity_params.target_atr_multiplier,
        )
        self.runs.append((geom, ticker, start, end))
        # Every grid point must go through the fetcher, or the memo under
        # test would never be exercised by these fakes.
        await self.fetcher.fetch_ohlcv(ticker, start, end)
        out = self.plan(geom, ticker, start, end)
        if isinstance(out, Exception):
            raise out
        return FakeResult(out)


def wins(n: int, pnl: float = 500.0) -> list[FakeTrade]:
    return [FakeTrade(pnl=pnl, exit_reason="target") for _ in range(n)]


def losses(n: int, pnl: float = -400.0) -> list[FakeTrade]:
    return [FakeTrade(pnl=pnl, exit_reason="stop") for _ in range(n)]


BASELINE = ExitGeometry(stop_atr_multiplier=2.0, target_atr_multiplier=4.0)


# ── ExitGeometry ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("stop,target", [(0.0, 4.0), (-1.0, 4.0), (2.0, 0.0), (2.0, -1.0)])
def test_geometry_rejects_non_positive_multipliers(stop, target):
    """compute_equity_trade_plan falls back to a raw ATR when the stop
    distance is zero, so a non-positive multiplier silently hands sizing to
    the fallback instead of the parameter under test."""
    with pytest.raises(ValueError):
        ExitGeometry(stop_atr_multiplier=stop, target_atr_multiplier=target)


def test_geometry_is_hashable_and_comparable():
    assert ExitGeometry(2.0, 4.0) == ExitGeometry(2.0, 4.0)
    assert len({ExitGeometry(2.0, 4.0), ExitGeometry(2.0, 4.0)}) == 1


def test_planned_reward_risk_is_target_over_stop():
    assert ExitGeometry(2.0, 4.0).planned_reward_risk == pytest.approx(2.0)
    assert ExitGeometry(2.0, 1.0).planned_reward_risk == pytest.approx(0.5)


def test_label_reads_as_atr_multiples():
    assert ExitGeometry(1.5, 1.0).label == "stop=1.5xATR/target=1xATR"


def test_apply_overrides_only_the_two_geometry_fields():
    """A caller sweeping geometry on top of tuned scoring weights must keep
    those weights — hence dataclasses.replace rather than a fresh
    EquitySignalParams()."""
    base = EquitySignalParams(rsi_oversold_threshold=11.0, risk_pct_per_trade=0.07)
    out = ExitGeometry(1.0, 1.25).apply(base)
    assert (out.stop_atr_multiplier, out.target_atr_multiplier) == (1.0, 1.25)
    assert out.rsi_oversold_threshold == 11.0
    assert out.risk_pct_per_trade == 0.07
    # and the base is not mutated
    assert base.stop_atr_multiplier == EquitySignalParams().stop_atr_multiplier


def test_apply_with_no_base_uses_shipped_defaults_for_other_fields():
    out = ExitGeometry(1.0, 1.0).apply()
    assert out.rsi_oversold_threshold == EquitySignalParams().rsi_oversold_threshold


# ── Grid ───────────────────────────────────────────────────────────────────

def test_expand_grid_is_cartesian_and_deterministic():
    g = expand_grid({"stop_atr_multiplier": [1.0, 2.0],
                     "target_atr_multiplier": [1.0, 3.0]})
    assert [(x.stop_atr_multiplier, x.target_atr_multiplier) for x in g] == [
        (1.0, 1.0), (1.0, 3.0), (2.0, 1.0), (2.0, 3.0),
    ]


@pytest.mark.parametrize("grid", [
    {},
    {"stop_atr_multiplier": [1.0]},
    {"target_atr_multiplier": [1.0]},
    {"stop_atr_multiplier": [], "target_atr_multiplier": [1.0]},
])
def test_expand_grid_rejects_missing_or_empty_axis(grid):
    with pytest.raises(ValueError):
        expand_grid(grid)


def test_shipped_grids_contain_the_shipped_geometry():
    """The baseline must be a grid point, or the comparison is against a
    separate re-run rather than like-for-like."""
    for grid in (EquityExitOptimizer.REDUCED_PARAM_GRID,
                 EquityExitOptimizer.PARAM_GRID):
        assert 2.0 in grid["stop_atr_multiplier"]
        assert 4.0 in grid["target_atr_multiplier"]


def test_default_grid_spans_targets_nearer_than_the_stop():
    """The hypothesis under test is that favourable excursion is ~1.1xATR,
    so the grid has to be able to express a target nearer than the stop."""
    geoms = expand_grid(EquityExitOptimizer.REDUCED_PARAM_GRID)
    assert any(g.target_atr_multiplier < g.stop_atr_multiplier for g in geoms)


# ── Trade pooling ──────────────────────────────────────────────────────────

def test_resolved_trades_drops_window_end_artifacts():
    r = FakeResult(wins(2) + [FakeTrade(pnl=0.0, exit_reason="backtest_end")])
    assert len(resolved_trades([r])) == 2
    assert unresolved_count([r]) == 1


def test_unresolved_trade_is_not_scored_as_a_breakeven():
    """run_equity hardcodes pnl=0.0 on the force-closed row. Counting it
    would drag the mean toward zero — worst for exactly the wide-target
    geometries under test, which leave positions open longest."""
    real = wins(3, pnl=300.0)
    with_artifact = real + [FakeTrade(pnl=0.0, exit_reason="backtest_end")]
    a = score_pooled(BASELINE, [FakeResult(real)], starting_capital=25000.0)
    b = score_pooled(BASELINE, [FakeResult(with_artifact)], starting_capital=25000.0)
    assert a.n_trades == b.n_trades == 3
    assert b.n_unresolved == 1
    assert a.sharpe == b.sharpe
    assert a.total_pnl == b.total_pnl


def test_score_pooled_pools_across_tickers():
    results = [FakeResult(wins(2)), FakeResult(losses(2))]
    p = score_pooled(BASELINE, results, starting_capital=25000.0)
    assert p.n_trades == 4


def test_score_pooled_marks_too_few_trades_unscorable():
    p = score_pooled(BASELINE, [FakeResult(wins(MIN_TRADES_TO_SCORE - 1))],
                     starting_capital=25000.0)
    assert p.sharpe == UNSCORABLE
    assert not p.is_scorable


def test_score_pooled_reports_exit_reason_mix():
    """Target-hit rate is the reachability measurement: an unreachable
    target shows up here before it shows up in Sharpe."""
    p = score_pooled(BASELINE, [FakeResult(wins(3) + losses(1))],
                     starting_capital=25000.0)
    assert p.target_hit_rate == pytest.approx(0.75)
    assert p.stop_hit_rate == pytest.approx(0.25)


def test_score_pooled_sums_recorded_equity_commissions():
    """Not strategy_optimizer's 4-leg options estimate, which overstates an
    equity fill by about an order of magnitude."""
    import app.services.equity_exit_optimizer as mod
    seen = {}
    real = mod.calculate_all_metrics

    def spy(**kwargs):
        seen.update(kwargs)
        return real(**kwargs)

    mod.calculate_all_metrics = spy
    try:
        trades = [FakeTrade(pnl=100.0, commission_paid=2.5) for _ in range(4)]
        score_pooled(BASELINE, [FakeResult(trades)], starting_capital=25000.0)
    finally:
        mod.calculate_all_metrics = real
    assert seen["total_commissions"] == pytest.approx(10.0)


def test_realized_reward_risk_is_zero_without_losses():
    p = score_pooled(BASELINE, [FakeResult(wins(4))], starting_capital=25000.0)
    assert p.realized_reward_risk == 0.0


def test_realized_reward_risk_matches_avg_win_over_avg_loss():
    p = GridPoint(geometry=BASELINE, sharpe=0.0, n_trades=4, n_unresolved=0,
                  win_rate=0.5, expectancy=0.0, profit_factor=1.0,
                  total_pnl=0.0, avg_win=600.0, avg_loss=-1800.0,
                  target_hit_rate=0.5, stop_hit_rate=0.5, avg_hold_days=3.0)
    assert p.realized_reward_risk == pytest.approx(1 / 3)


# ── Memo fetcher ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_memo_fetcher_serves_repeats_from_cache():
    inner = FakeFetcher()
    memo = _MemoFetcher(inner)
    a = await memo.fetch_ohlcv("MRVL", "2026-01-01", "2026-06-01")
    b = await memo.fetch_ohlcv("mrvl", "2026-01-01", "2026-06-01")
    assert a == b
    assert len(inner.calls) == 1
    assert (memo.misses, memo.hits) == (1, 1)


@pytest.mark.asyncio
async def test_memo_fetcher_separates_distinct_windows():
    inner = FakeFetcher()
    memo = _MemoFetcher(inner)
    await memo.fetch_ohlcv("MRVL", "2026-01-01", "2026-06-01")
    await memo.fetch_ohlcv("MRVL", "2026-06-01", "2026-07-01")
    assert len(inner.calls) == 2


def test_memo_fetcher_delegates_unknown_attributes():
    assert _MemoFetcher(FakeFetcher()).sentinel == "inner-attr"


# ── Windows ────────────────────────────────────────────────────────────────

def test_validation_window_starts_where_training_ends():
    ts, te, vs, ve = EquityExitOptimizer.windows("2026-10-17", 6, 1)
    assert te == vs
    assert ts < te < ve
    assert ve == "2026-10-17"


@pytest.mark.parametrize("train,validate", [(0, 1), (6, 0), (-1, 1)])
def test_windows_reject_non_positive_months(train, validate):
    with pytest.raises(ValueError):
        EquityExitOptimizer.windows("2026-10-17", train, validate)


# ── Sweep ──────────────────────────────────────────────────────────────────

def _opt(plan, **kw) -> tuple[EquityExitOptimizer, FakeBacktester]:
    bt = FakeBacktester(plan)
    return EquityExitOptimizer(bt, **kw), bt


TINY_GRID = {"stop_atr_multiplier": [1.0, 2.0],
             "target_atr_multiplier": [1.0, 4.0]}

END_DATE = "2026-10-17"
# Derived, never hardcoded: a stale literal here would make every
# `start == TRAIN_START` check False and quietly turn the train/validate
# tests below into tests of nothing.
TRAIN_START, _TRAIN_END, VALIDATE_START, _VALIDATE_END = \
    EquityExitOptimizer.windows(END_DATE, 6, 1)
assert TRAIN_START != VALIDATE_START


@pytest.mark.asyncio
async def test_sweep_requires_a_ticker():
    opt, _ = _opt(lambda *a: wins(4))
    with pytest.raises(ValueError):
        await opt.sweep([])


@pytest.mark.asyncio
async def test_sweep_scores_every_geometry_on_every_ticker():
    opt, bt = _opt(lambda *a: wins(4))
    res = await opt.sweep(["MRVL", "INTC"], end_date="2026-10-17", grid=TINY_GRID)
    assert res.grid_points_tested == 4
    train_runs = [r for r in bt.runs if r[2] == res.train_start]
    assert len(train_runs) == 4 * 2


@pytest.mark.asyncio
async def test_sweep_memoizes_ohlcv_across_grid_points_and_restores_fetcher():
    opt, bt = _opt(lambda *a: wins(4))
    inner = bt.fetcher
    await opt.sweep(["MRVL"], end_date="2026-10-17", grid=TINY_GRID)
    assert bt.fetcher is inner, "sweep must restore the caller's fetcher"
    # 4 geometries x 1 ticker on train + validation runs, but only 2
    # distinct (ticker, window) pairs, so 2 real fetches.
    assert len(inner.calls) == 2


@pytest.mark.asyncio
async def test_sweep_restores_fetcher_even_when_scoring_raises():
    import app.services.equity_exit_optimizer as mod
    opt, bt = _opt(lambda *a: wins(4))
    inner = bt.fetcher
    real = mod.calculate_all_metrics
    mod.calculate_all_metrics = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        with pytest.raises(RuntimeError):
            await opt.sweep(["MRVL"], end_date="2026-10-17", grid=TINY_GRID)
    finally:
        mod.calculate_all_metrics = real
    assert bt.fetcher is inner


@pytest.mark.asyncio
async def test_one_failing_ticker_does_not_void_the_grid_point():
    def plan(geom, ticker, start, end):
        if ticker == "BROKEN":
            return ValueError("no OHLCV data returned")
        return wins(5)

    opt, _ = _opt(plan)
    res = await opt.sweep(["MRVL", "BROKEN"], end_date="2026-10-17", grid=TINY_GRID)
    assert res.best_on_train is not None
    assert res.best_on_train.n_trades == 5


@pytest.mark.asyncio
async def test_baseline_winning_training_is_reported_not_accepted():
    """Nothing to recommend when the shipped geometry already wins."""
    def plan(geom, ticker, start, end):
        return wins(10) if geom == BASELINE else losses(10)

    opt, _ = _opt(plan)
    res = await opt.sweep(["MRVL"], end_date="2026-10-17", grid=TINY_GRID)
    assert res.best_on_train.geometry == BASELINE
    assert res.accepted is False
    assert "already won" in res.rejection_reason
    assert res.recommended == BASELINE


@pytest.mark.asyncio
async def test_candidate_accepted_when_validation_confirms():
    near = ExitGeometry(1.0, 1.0)

    def plan(geom, ticker, start, end):
        # Consistent in both windows, and well clear of the gain floor.
        return wins(12, pnl=900.0) if geom == near else losses(12, pnl=-300.0)

    opt, _ = _opt(plan)
    res = await opt.sweep(["MRVL"], end_date="2026-10-17", grid=TINY_GRID)
    assert res.best_on_train.geometry == near
    assert res.accepted is True, res.rejection_reason
    assert res.recommended == near
    assert res.validation_sharpe_gain > 0


@pytest.mark.asyncio
async def test_candidate_rejected_when_out_of_sample_sample_is_too_small():
    near = ExitGeometry(1.0, 1.0)

    def plan(geom, ticker, start, end):
        on_train = start == TRAIN_START
        if geom == near:
            return wins(12) if on_train else wins(4)
        return losses(12) if on_train else losses(4)

    opt, _ = _opt(plan)
    res = await opt.sweep(["MRVL"], end_date="2026-10-17", grid=TINY_GRID)
    assert res.accepted is False
    assert "out-of-sample trades" in res.rejection_reason
    assert res.recommended == BASELINE


@pytest.mark.asyncio
async def test_candidate_rejected_when_gain_does_not_clear_the_floor():
    """A candidate that merely ties out-of-sample is noise, not an edge."""
    near = ExitGeometry(1.0, 1.0)

    def plan(geom, ticker, start, end):
        on_train = start == TRAIN_START
        if on_train:
            return wins(12, pnl=900.0) if geom == near else losses(12)
        return wins(6, pnl=500.0) + losses(6, pnl=-500.0)

    opt, _ = _opt(plan)
    res = await opt.sweep(["MRVL"], end_date="2026-10-17", grid=TINY_GRID)
    assert res.accepted is False
    assert "below the" in res.rejection_reason
    assert res.recommended == BASELINE


@pytest.mark.asyncio
async def test_unscorable_training_window_rejects_without_validating():
    opt, bt = _opt(lambda *a: wins(1))
    res = await opt.sweep(["MRVL"], end_date="2026-10-17", grid=TINY_GRID)
    assert res.best_on_train is None
    assert res.accepted is False
    assert "nothing to score" in res.rejection_reason
    assert res.baseline_on_validate is None
    assert all(r[2] == res.train_start for r in bt.runs), \
        "must not spend validation backtests when training produced nothing"


@pytest.mark.asyncio
async def test_baseline_geometry_is_read_from_base_params():
    """A sweep run against non-default settings must compare to what is
    actually shipped there, not to a hardcoded 2.0/4.0."""
    opt, _ = _opt(lambda *a: wins(4),
                  base_params=EquitySignalParams(stop_atr_multiplier=1.5,
                                                 target_atr_multiplier=2.5))
    assert opt.baseline_geometry == ExitGeometry(1.5, 2.5)


@pytest.mark.asyncio
async def test_sweep_passes_starting_capital_through():
    seen = []

    class Bt(FakeBacktester):
        async def run_equity(self, ticker, start, end, starting_capital=25000.0,
                             equity_params=None):
            seen.append(starting_capital)
            return await super().run_equity(
                ticker, start, end, starting_capital=starting_capital,
                equity_params=equity_params)

    bt = Bt(lambda *a: wins(4))
    opt = EquityExitOptimizer(bt, starting_capital=250000.0)
    await opt.sweep(["MRVL"], end_date="2026-10-17", grid=TINY_GRID)
    assert set(seen) == {250000.0}


# ── Environment guard ──────────────────────────────────────────────────────

def test_module_does_not_import_backtester_at_runtime():
    """The Backtester import stays behind TYPE_CHECKING so this module and
    its tests collect in the cloud container, where `ta` does not build.
    Asserts the module is importable with backtester absent rather than
    merely that it is not already in sys.modules."""
    import importlib
    saved = {k: v for k, v in sys.modules.items()
             if k in ("app.services.backtester", "app.services.equity_exit_optimizer")}
    for k in saved:
        sys.modules.pop(k, None)
    sys.modules["app.services.backtester"] = None  # poison: import would raise
    try:
        importlib.import_module("app.services.equity_exit_optimizer")
    finally:
        sys.modules.pop("app.services.backtester", None)
        sys.modules.update(saved)


# ── Acceptance gates, exercised directly ───────────────────────────────────
# _decide is where a sweep turns into a recommendation, so each gate gets a
# test against constructed GridPoints. Driving these end-to-end would mean
# reverse-engineering pnl series that land on particular Sharpes, which
# tests calculate_all_metrics rather than the gate.

def _gp(geometry: ExitGeometry, sharpe: float, n: int = 20) -> GridPoint:
    return GridPoint(
        geometry=geometry, sharpe=sharpe, n_trades=n, n_unresolved=0,
        win_rate=0.5, expectancy=0.0, profit_factor=1.0, total_pnl=0.0,
        avg_win=100.0, avg_loss=-100.0, target_hit_rate=0.5,
        stop_hit_rate=0.5, avg_hold_days=4.0,
    )


NEAR = ExitGeometry(1.0, 1.0)


def test_decide_rejects_a_candidate_that_mostly_decayed_out_of_sample():
    """Training gain 4.00, out-of-sample gain 0.30: clears the absolute
    floor but retains under 25% of the edge, which reads as fitted to the
    training window rather than a real improvement."""
    opt = EquityExitOptimizer(object())
    accepted, reason = opt._decide(
        BASELINE,
        _gp(NEAR, 4.5),
        [_gp(NEAR, 4.5), _gp(BASELINE, 0.5)],
        _gp(BASELINE, 0.5),
        _gp(NEAR, 0.8),
    )
    assert accepted is False
    assert "fitted to the training window" in reason


def test_decide_accepts_when_the_edge_largely_survives():
    opt = EquityExitOptimizer(object())
    accepted, reason = opt._decide(
        BASELINE,
        _gp(NEAR, 1.2),
        [_gp(NEAR, 1.2), _gp(BASELINE, 0.3)],
        _gp(BASELINE, 0.3),
        _gp(NEAR, 1.1),
    )
    assert accepted is True, reason
    assert reason is None


def test_decide_skips_the_decay_gate_when_the_baseline_was_unscorable():
    """An unscorable baseline has no training Sharpe to measure decay
    against. Treating its -999 as a real score would make every training
    gain look enormous and reject every candidate."""
    opt = EquityExitOptimizer(object())
    unscorable_baseline = _gp(BASELINE, UNSCORABLE, n=1)
    assert not unscorable_baseline.is_scorable
    accepted, reason = opt._decide(
        BASELINE,
        _gp(NEAR, 1.0),
        [_gp(NEAR, 1.0), unscorable_baseline],
        _gp(BASELINE, 0.1),
        _gp(NEAR, 0.9),
    )
    assert accepted is True, reason


def test_decide_rejects_when_validation_did_not_run():
    opt = EquityExitOptimizer(object())
    accepted, reason = opt._decide(
        BASELINE, _gp(NEAR, 1.0), [_gp(NEAR, 1.0)], None, None,
    )
    assert accepted is False
    assert "did not run" in reason


@pytest.mark.asyncio
async def test_validation_sharpe_gain_is_none_when_validation_did_not_run():
    opt, _ = _opt(lambda *a: wins(1))
    res = await opt.sweep(["MRVL"], end_date=END_DATE, grid=TINY_GRID)
    assert res.best_on_validate is None
    assert res.validation_sharpe_gain is None


@pytest.mark.asyncio
async def test_sweep_runs_against_a_backtester_with_no_fetcher():
    """Memoization is an optimization, not a requirement — a backtester
    exposing no fetcher must still sweep rather than raise."""
    class NoFetcher:
        async def run_equity(self, ticker, start, end, starting_capital=25000.0,
                             equity_params=None):
            return FakeResult(wins(10))

    res = await EquityExitOptimizer(NoFetcher()).sweep(
        ["MRVL"], end_date=END_DATE, grid=TINY_GRID)
    assert res.best_on_train is not None
    assert res.best_on_train.n_trades == 10


# ── Runner guard ───────────────────────────────────────────────────────────

def test_sweep_runner_opens_no_broker_connection():
    """scripts/equity_exit_sweep.py is research tooling. It must reach
    market data without get_broker(), so running it cannot touch the live
    execution stack or consume an IBKR client id — OHLCV comes from
    yfinance via a stub broker that reports no bars.

    Scans the script text rather than importing it: the script imports
    Backtester (and so `ta`) inside main(), which does not build in the
    cloud container.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "scripts" / "equity_exit_sweep.py"
    text = src.read_text()
    assert len(text) > 1000, f"guard read nothing useful from {src}"
    assert "get_broker" not in text, (
        "the sweep runner must not construct a live broker"
    )
    # and it must actually be wiring the stub into DataFetcher
    assert "DataFetcher(_YFinanceOnly())" in text
    assert "async def get_bars" in text
