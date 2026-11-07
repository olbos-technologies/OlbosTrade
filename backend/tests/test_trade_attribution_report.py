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
        commission_paid=None if commission is None else Decimal(str(commission)),
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



# ---------- "not recorded" is not "zero" ----------
#
# commission_paid is written only by the backtester, so on a live or paper book
# it is NULL on every row. The report used to sum it through _f(), which turns
# NULL into 0.0, and printed "commission paid : 0.00" beside a "gross P&L" equal
# to the net figure. A missing measurement was reported as a clean one — the
# precise failure this tool exists to refuse.

def test_unrecorded_commissions_are_not_reported_as_zero(monkeypatch, capsys):
    _run(monkeypatch, [_trade(d, commission=None) for d in range(40)])
    out = capsys.readouterr().out
    assert "commission recorded on : 0 of 40 trades" in out
    assert "NOT the same as none being paid" in out
    assert "commission paid        : 0.00" not in out, (
        "a NULL commission was summed to 0.00 and presented as a measurement"
    )
    assert "commission as %" not in out, "no cost-drag share can be computed from nothing"


def test_partial_coverage_is_stated_and_scoped_to_the_trades_that_carry_one(monkeypatch, capsys):
    trades = (
        [_trade(d, pnl=10.0, commission=2.0) for d in range(10)]
        + [_trade(d + 10, pnl=10.0, commission=None) for d in range(30)]
    )
    _run(monkeypatch, trades)
    out = capsys.readouterr().out
    assert "commission recorded on : 10 of 40 trades" in out
    assert "only the 10 trades that carry one" in out
    assert "30 trades have no commission recorded" in out


def test_full_coverage_reads_without_caveats(monkeypatch, capsys):
    _run(monkeypatch, [_trade(d, pnl=100.0, commission=1.0) for d in range(40)])
    out = capsys.readouterr().out
    assert "commission recorded on : 40 of 40 trades" in out
    assert "only the" not in out.split("7 COST DRAG")[1].split("8 SCORE")[0]
    assert "have no commission recorded" not in out


# ---------- sections 9-12: payoff, confidence buckets, style, asset class ----------

def _t(day=0, pnl=10.0, score=0.7, strategy="equity", spread_type="equity_long",
       style="aggressive", regime="normal_mean_revert"):
    t = _trade(day_offset=day, pnl=pnl, strategy=strategy, regime=regime, score=score)
    t.spread_type = spread_type
    t.trading_mode_at_entry = style
    return t


@pytest.mark.parametrize("score, expected", [
    (0.0001, "< 0.50"), (0.4999, "< 0.50"),
    (0.50, "0.50 - 0.59"), (0.5999, "0.50 - 0.59"),
    (0.60, "0.60 - 0.69"), (0.6999, "0.60 - 0.69"),
    (0.70, ">= 0.70"), (0.99, ">= 0.70"), (1.0, ">= 0.70"),
])
def test_confidence_bucket_boundaries_belong_to_the_higher_bucket(score, expected):
    """0.70 is the Aggressive routing bar: a trade AT the bar must read as meeting it."""
    assert mod.confidence_bucket(score) == expected
    assert mod.confidence_bucket(Decimal(str(score))) == expected


@pytest.mark.parametrize("score", [None, 0, 0.0, Decimal("0"), -0.2, 1.5])
def test_a_missing_zero_or_impossible_score_is_unscored_not_low(score):
    """A stored 0 is the old 'no score supplied' fallback; it is not a 0%-confidence
    trade, and filing it under '< 0.50' would make the lowest bucket look worse."""
    assert mod.confidence_bucket(score) == mod.UNSCORED


def test_payoff_matches_a_hand_worked_example():
    s = mod.payoff_summary([30.0, 30.0, -10.0, -10.0, -10.0])
    assert (s["n_wins"], s["n_losses"]) == (2, 3)
    assert s["avg_win"] == 30.0 and s["avg_loss"] == -10.0
    assert s["payoff_ratio"] == pytest.approx(3.0)
    assert s["win_rate"] == pytest.approx(0.40)
    assert s["breakeven_win_rate"] == pytest.approx(0.25)     # 1 / (1 + 3)


def test_a_high_win_rate_can_still_be_below_break_even():
    """The reason the number is printed: 90% winners at 1 against one -20 loser."""
    s = mod.payoff_summary([1.0] * 9 + [-20.0])
    assert s["win_rate"] == pytest.approx(0.90)
    assert s["breakeven_win_rate"] == pytest.approx(1 / (1 + 1 / 20))   # 95.2%
    assert s["win_rate"] < s["breakeven_win_rate"]


def test_payoff_needs_both_sides_and_does_not_invent_a_ratio():
    for pnls in ([5.0, 7.0], [-5.0, -7.0], []):
        s = mod.payoff_summary(pnls)
        assert s["payoff_ratio"] != s["payoff_ratio"]            # NaN
        assert s["breakeven_win_rate"] != s["breakeven_win_rate"]


def test_a_flat_trade_is_neither_a_win_nor_a_loss():
    s = mod.payoff_summary([10.0, 0.0, -10.0])
    assert (s["n_wins"], s["n_losses"], s["n_flat"]) == (1, 1, 1)
    assert s["win_rate"] == pytest.approx(1 / 3)


def test_the_payoff_section_prints_the_arithmetic_and_the_gap(monkeypatch, capsys):
    trades = [_t(day=i, pnl=p) for i, p in enumerate([30, 30, -10, -10, -10])]
    _run(monkeypatch, trades)
    out = capsys.readouterr().out
    assert "9 PAYOFF" in out
    assert "avg win=   +30.00 (n=2)" in out and "avg loss=   -10.00 (n=3)" in out
    assert "payoff=3.00:1" in out
    assert "win rate  40.0% vs  25.0% needed to break even" in out and "+15.0 pts" in out


def test_an_all_winning_book_does_not_get_a_payoff_ratio(monkeypatch, capsys):
    _run(monkeypatch, [_t(day=i, pnl=10) for i in range(5)])
    out = capsys.readouterr().out
    assert "only winners recorded -- no payoff ratio exists" in out


def test_the_confidence_table_files_each_trade_in_the_right_bucket(monkeypatch, capsys):
    trades = (
        [_t(day=i, score=0.45) for i in range(3)]
        + [_t(day=10 + i, score=0.55) for i in range(4)]
        + [_t(day=20 + i, score=0.65) for i in range(5)]
        + [_t(day=30 + i, score=0.75) for i in range(6)]
        + [_t(day=40, score=None), _t(day=41, score=0)]
    )
    _run(monkeypatch, trades)
    out = capsys.readouterr().out
    section = out.split("10 BY CONFIDENCE BUCKET")[1].split("11 BY TRADING STYLE")[0]
    for label, n in (("< 0.50", 3), ("0.50 - 0.59", 4), ("0.60 - 0.69", 5), (">= 0.70", 6),
                     ("(unscored)", 2)):
        line = next(l for l in section.splitlines() if l.strip().startswith(label))
        assert f"n={n}" in line, line
    assert "scored desk trades: 18 of 20" in section


def test_buckets_without_enough_trades_are_never_compared(monkeypatch, capsys):
    """The honest state of a young journal: say the buckets cannot be compared, and
    never read that as 'a lower bar is safe' or 'unsafe'."""
    trades = [_t(day=i, score=0.45, pnl=-5) for i in range(5)] + \
             [_t(day=10 + i, score=0.75, pnl=20) for i in range(5)]
    _run(monkeypatch, trades)
    section = capsys.readouterr().out.split("10 BY CONFIDENCE BUCKET")[1].split("11 BY")[0]
    assert "NOT INTERPRETABLE" in section
    assert "Fewer than two buckets reach n>=30" in section
    assert "SEPARATED" not in section
    assert "NOT say a lower routing bar is safe or unsafe" in section


def test_overlapping_buckets_are_refused_a_ranking(monkeypatch, capsys):
    pattern = [20.0, -20.0]
    low = [_t(day=i, score=0.45, pnl=pattern[i % 2]) for i in range(40)]
    high = [_t(day=100 + i, score=0.75, pnl=pattern[i % 2]) for i in range(40)]
    _run(monkeypatch, low + high)
    section = capsys.readouterr().out.split("10 BY CONFIDENCE BUCKET")[1].split("11 BY")[0]
    assert "No two buckets are separated" in section
    assert "SEPARATED" not in section


def test_a_genuinely_separated_bucket_pair_is_reported_and_still_needs_a_forward_test(
    monkeypatch, capsys,
):
    low = [_t(day=i, score=0.45, pnl=-20.0 - (i % 3)) for i in range(40)]
    high = [_t(day=100 + i, score=0.75, pnl=20.0 + (i % 3)) for i in range(40)]
    _run(monkeypatch, low + high)
    section = capsys.readouterr().out.split("10 BY CONFIDENCE BUCKET")[1].split("11 BY")[0]
    assert "SEPARATED: bucket >= 0.70 beats < 0.50" in section
    assert "Forward-test any threshold change on paper" in section


def test_inherited_positions_are_not_scored_buckets(monkeypatch, capsys):
    """An adopted position was chosen by no score; counting it would dilute a bucket."""
    adopted = _t(day=0, pnl=-50, score=0.80, strategy="adopted_untracked")
    desk = [_t(day=1 + i, score=0.75, pnl=10) for i in range(3)]
    _run(monkeypatch, desk + [adopted])
    section = capsys.readouterr().out.split("10 BY CONFIDENCE BUCKET")[1].split("11 BY")[0]
    line = next(l for l in section.splitlines() if l.strip().startswith(">= 0.70"))
    assert "n=3" in line
    assert "scored desk trades: 3 of 3" in section


def test_the_payoff_section_splits_desk_from_inherited_when_both_exist(monkeypatch, capsys):
    adopted = _t(day=0, pnl=-50, strategy="adopted_untracked")
    desk = [_t(day=1, pnl=10), _t(day=2, pnl=-5)]
    _run(monkeypatch, desk + [adopted])
    section = capsys.readouterr().out.split("9 PAYOFF")[1].split("10 BY")[0]
    assert "all closed trades" in section and "desk-originated only" in section


def test_styles_and_asset_classes_are_each_tabulated(monkeypatch, capsys):
    trades = (
        [_t(day=i, style="aggressive") for i in range(4)]
        + [_t(day=10 + i, style="balanced") for i in range(2)]
        + [_t(day=20, style=None)]
        + [_t(day=30 + i, strategy="bull_put_spread", spread_type="put") for i in range(3)]
    )
    _run(monkeypatch, trades)
    out = capsys.readouterr().out
    style = out.split("11 BY TRADING STYLE")[1].split("12 BY ASSET CLASS")[0]
    assert "aggressive" in style and "balanced" in style and "(unrecorded)" in style
    klass = out.split("12 BY ASSET CLASS")[1]
    equity = next(l for l in klass.splitlines() if l.strip().startswith("equity"))
    options = next(l for l in klass.splitlines() if l.strip().startswith("options"))
    assert "n=7" in equity            # 4 + 2 + 1 equity rows
    assert "n=3" in options


def test_the_new_sections_survive_the_shapes_that_break_report_code(monkeypatch, capsys):
    """No scores, no styles, no spread_type attribute at all, a single trade."""
    bare = SimpleNamespace(
        entry_date=datetime(2026, 8, 3, tzinfo=timezone.utc), exit_date=None,
        pnl=Decimal("1"), strategy=None, exit_reason=None, regime=None,
        underlying="X", trading_mode_at_entry=None, signal_score=None,
        commission_paid=None, pnl_capture_pct=None, mfe_pnl=None, mae_pnl=None,
    )
    _run(monkeypatch, [bare])
    out = capsys.readouterr().out
    for heading in ("9 PAYOFF", "10 BY CONFIDENCE BUCKET", "11 BY TRADING STYLE",
                    "12 BY ASSET CLASS"):
        assert heading in out


def test_the_new_sections_keep_the_multiplicity_warning_last(monkeypatch, capsys):
    _run(monkeypatch, [_t(day=i) for i in range(5)])
    out = capsys.readouterr().out
    assert out.index("12 BY ASSET CLASS") < out.index("Multiplicity: several cells")
