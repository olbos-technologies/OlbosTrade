"""
The attribution report must survive the data it will actually meet.

A diagnostic script that raises on the production box is worse than no script:
it is run once, under time pressure, by someone trying to answer "why are we
losing money". So this exercises the statistics directly, and drives the whole
report over the shapes that break report code in practice — an empty window,
a single trading day, every trade a loser, missing signal_score, missing
regime, zero commission.

No database: the session is stubbed, so this runs anywhere.
"""
import asyncio
import importlib.util
import pathlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

SCRIPT = (
    pathlib.Path(__file__).resolve().parents[1] / "scripts" / "trade_attribution_report.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("trade_attribution_report", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = _load()


def _trade(day_offset=0, pnl=10.0, strategy="bull_put_spread", exit_reason="profit_target",
           regime="normal_mean_revert", score=0.7, commission=1.0, capture=0.5):
    entry = datetime(2026, 8, 3, tzinfo=timezone.utc) + timedelta(days=day_offset)
    return SimpleNamespace(
        entry_date=entry, exit_date=entry + timedelta(days=2),
        pnl=Decimal(str(pnl)), strategy=strategy, exit_reason=exit_reason,
        regime=regime, underlying="SPY", trading_mode_at_entry="paper",
        signal_score=None if score is None else Decimal(str(score)),
        commission_paid=Decimal(str(commission)),
        pnl_capture_pct=None if capture is None else Decimal(str(capture)),
        mfe_pnl=Decimal("20"), mae_pnl=Decimal("-5"),
    )


def _run(monkeypatch, trades, **kwargs):
    class _Result:
        def scalars(self):
            return SimpleNamespace(all=lambda: trades)

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, _stmt):
            return _Result()

    monkeypatch.setattr(mod, "AsyncSessionLocal", lambda: _Session())
    asyncio.run(mod.main(kwargs.get("days", 120), kwargs.get("mode")))


# ---------- the statistics ----------

def test_clustered_bootstrap_resamples_days_not_trades():
    """
    Twenty trades on two days carry two days of information, not twenty.

    This is the whole reason the report exists in this form: resampling trades
    individually would return a tight interval around a two-day sample and
    make a noise result look decisive.
    """
    rows = (
        [("2026-08-03", 10.0)] * 10
        + [("2026-08-04", 10.0)] * 10
        + [("2026-08-05", 10.0)] * 10
        + [("2026-08-06", -30.0)] * 10
    )
    mean, lo, hi = mod.clustered_bootstrap_mean(rows, reps=2000)
    assert mean == pytest.approx(0.0)
    assert lo <= 0 <= hi, "an interval over opposing days must span zero"
    # Resampling the 40 trades individually would give roughly +/-5.4 here
    # (sd 17.3 / sqrt(40)). Resampling the 4 DAYS has to be far wider, because
    # four days carry four days of information however many trades sit on them.
    # If this width ever collapses toward 11, the clustering has been lost and
    # every interval in the report is overconfident.
    assert hi - lo > 20, f"clustering collapsed: width {hi - lo:.1f} looks trade-level"


def test_interval_is_refused_rather_than_faked_on_too_few_days():
    mean, lo, hi = mod.clustered_bootstrap_mean([("2026-08-03", 5.0), ("2026-08-04", 7.0)])
    assert mean == pytest.approx(6.0)
    assert lo != lo and hi != hi, "fewer than 3 days must yield NaN, not a confident interval"


def test_auc_is_a_coin_flip_when_scores_carry_no_information():
    assert mod.auc([0.5, 0.5, 0.5], [0.5, 0.5]) == pytest.approx(0.5)
    assert mod.auc([0.9, 0.8], [0.1, 0.2]) == pytest.approx(1.0)
    assert mod.auc([0.1, 0.2], [0.9, 0.8]) == pytest.approx(0.0)


def test_auc_undefined_without_both_classes():
    assert mod.auc([], [0.3]) != mod.auc([], [0.3])  # NaN


def test_profit_factor_handles_the_no_loss_and_no_trade_cases():
    assert mod.profit_factor([10.0, -5.0]) == pytest.approx(2.0)
    assert mod.profit_factor([10.0, 5.0]) == float("inf")
    assert mod.profit_factor([]) != mod.profit_factor([])  # NaN


def test_a_high_win_rate_losing_strategy_is_not_scored_as_good():
    """Nine +1s and one -20 is a losing strategy with a 90% win rate."""
    assert mod.profit_factor([1.0] * 9 + [-20.0]) < 1.0


# ---------- the report ----------

def test_empty_window_says_so_instead_of_crashing(monkeypatch, capsys):
    _run(monkeypatch, [])
    out = capsys.readouterr().out
    assert "No closed trades" in out
    assert "entry-gate question" in out


def test_single_day_of_trades_does_not_produce_a_confident_interval(monkeypatch, capsys):
    _run(monkeypatch, [_trade(0, pnl=p) for p in (5, -3, 8)])
    out = capsys.readouterr().out
    assert "CI n/a" in out
    assert "too few trading days" in out


def test_an_all_losing_book_is_reported_as_negative_not_hedged(monkeypatch, capsys):
    _run(monkeypatch, [_trade(d, pnl=-20.0) for d in range(40)])
    out = capsys.readouterr().out
    assert "NEGATIVE with 95%" in out
    assert "Position sizing and trade frequency" in out


def test_overlapping_strategies_are_refused_a_ranking(monkeypatch, capsys):
    trades = (
        [_trade(d, pnl=1.0, strategy="bull_put_spread") for d in range(40)]
        + [_trade(d, pnl=1.0, strategy="bear_call_spread") for d in range(40)]
    )
    _run(monkeypatch, trades)
    out = capsys.readouterr().out
    assert "Do not reallocate" in out
    assert "SEPARATED:" not in out


def test_a_genuinely_separated_pair_is_reported(monkeypatch, capsys):
    trades = (
        [_trade(d, pnl=50.0, strategy="bull_put_spread") for d in range(40)]
        + [_trade(d, pnl=-50.0, strategy="bear_call_spread") for d in range(40)]
    )
    _run(monkeypatch, trades)
    out = capsys.readouterr().out
    assert "SEPARATED: bull_put_spread beats bear_call_spread" in out


def test_small_cells_are_marked_and_never_ranked(monkeypatch, capsys):
    trades = (
        [_trade(d, pnl=50.0, strategy="bull_put_spread") for d in range(40)]
        + [_trade(d, pnl=-50.0, strategy="rare_strategy") for d in range(4)]
    )
    _run(monkeypatch, trades)
    out = capsys.readouterr().out
    assert "NOT INTERPRETABLE" in out
    assert "rare_strategy" not in out.split("SEPARATED")[-1] or "SEPARATED:" not in out


def test_missing_score_and_regime_are_reported_as_absent_not_as_values(monkeypatch, capsys):
    _run(monkeypatch, [_trade(d, score=None, regime=None) for d in range(40)])
    out = capsys.readouterr().out
    assert "(unrecorded)" in out, "a missing regime must be visible, not silently bucketed"
    assert "Too few scored winners or losers" in out


def test_cost_drag_is_flagged_when_it_eats_the_edge(monkeypatch, capsys):
    _run(monkeypatch, [_trade(d, pnl=1.0, commission=1.0) for d in range(40)])
    out = capsys.readouterr().out
    assert "Costs are eating more than 30%" in out
    assert "a sizing change, not" in out


def test_a_score_with_no_information_is_called_absent_not_inverted(monkeypatch, capsys):
    """
    The trap this guards: a point estimate below 0.5 on ~40 trades reads as an
    inverted signal, and someone flips the sign. With the interval printed it
    reads as what it is — nothing measurable either way.
    """
    trades = (
        [_trade(d, pnl=10.0, score=0.5) for d in range(20)]
        + [_trade(d, pnl=-10.0, score=0.5) for d in range(20)]
    )
    _run(monkeypatch, trades)
    out = capsys.readouterr().out
    assert "NO MEASURABLE SKILL" in out
    assert "it is an absent one" in out
    assert "do not raise the threshold" in out


def test_the_auc_carries_an_interval_like_every_other_number(monkeypatch, capsys):
    trades = (
        [_trade(d, pnl=10.0, score=0.6) for d in range(20)]
        + [_trade(d, pnl=-10.0, score=0.4) for d in range(20)]
    )
    _run(monkeypatch, trades)
    out = capsys.readouterr().out
    assert "AUC" in out and ("[" in out.split("AUC")[1].split("\n")[0])


def test_inherited_positions_are_split_out_from_the_desks_own_trades(monkeypatch, capsys):
    trades = (
        [_trade(d, pnl=100.0, strategy="equity") for d in range(20)]
        + [_trade(d, pnl=-300.0, strategy="adopted_untracked") for d in range(20)]
    )
    _run(monkeypatch, trades)
    out = capsys.readouterr().out
    assert "DESK vs INHERITED" in out
    assert "no strategy chose it" in out
    assert "the account disagree" in out, (
        "a positive desk line against a negative account must be stated, not averaged away"
    )


def test_the_cross_tab_locates_which_book_is_hitting_the_stops(monkeypatch, capsys):
    trades = (
        [_trade(d, pnl=50.0, strategy="equity", exit_reason="profit_target")
         for d in range(10)]
        + [_trade(d, pnl=-900.0, strategy="adopted_untracked", exit_reason="stop_hit")
           for d in range(10)]
    )
    _run(monkeypatch, trades)
    out = capsys.readouterr().out
    assert "STRATEGY x EXIT REASON" in out
    row = [ln for ln in out.splitlines() if ln.strip().startswith("stop_hit")][0]
    assert "-9000.00" in row and row.index("-9000.00") > row.index("stop_hit")


def test_self_selected_exit_buckets_carry_their_caveat(monkeypatch, capsys):
    _run(monkeypatch, [_trade(d) for d in range(40)])
    out = capsys.readouterr().out
    assert "SELF-SELECTED" in out
    assert "disposition" in out and "effect" in out
    assert "loss-realiser" in out, (
        "rotation closes only losing positions by construction "
        "(position_rotation.py:152); without saying so, its profit factor near "
        "zero reads as a defect and someone 'fixes' a working control"
    )
    assert "designed value, not a defect" in out
    assert "the totals are not" in out


def test_the_report_never_claims_authority_to_change_live_trading(monkeypatch, capsys):
    _run(monkeypatch, [_trade(d) for d in range(40)])
    out = capsys.readouterr().out
    assert "Nothing here authorises a" in out and "live change" in out
    assert "validate on paper first" in out
