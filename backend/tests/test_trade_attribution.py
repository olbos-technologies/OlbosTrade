"""Adopted positions are counted as money and separated as attribution.

Measured on production (2026-10-17, three months of paper trading):

    strategy            n    total P&L    win %
    equity             43    +3,163.51     53.5
    adopted_untracked  25    -4,263.07     48.0
    ------------------------------------------
    combined           68    -1,099.56     51.5

The first version of this module got the reason wrong and the behaviour with
it. It claimed the adopted rows' cost basis was invented and excluded their
P&L from every total, which would have reported +3,163 on a book that was
really down 1,099. **That is the more dangerous of the two possible errors**
— it reads as "we are fine" while capital drains — and this file now pins
against it.

The adopt path writes `pos.avg_cost` into `credit_received`, and equity P&L
is computed from `credit_received`. `avg_cost` is the broker's real average
cost basis, so an adopted equity position's P&L is correct arithmetic on real
money. What IS a placeholder is `entry_date` and the strike fields.

So the split is about attribution, not arithmetic:

1. Adopted P&L is IN total_pnl, win rate and expectancy. It is real.
2. It is also broken out, because no strategy chose those positions — the
   desk's decisions and the account can disagree, and on production they did.
3. Nothing is dropped silently, the way `unmeasured_trades` travels with
   performance and `heat_overstated` with portfolio heat.

What is NOT tested here, deliberately, because it must not change: adopted
positions still count for exposure, heat, concurrent-position limits and
reconciliation. `trade_attribution` is imported by none of those paths, and
`test_risk_paths_still_count_adopted_positions` pins that.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services.performance_analytics import compute_performance
from app.services.trade_attribution import ADOPTED_STRATEGY, is_adopted, partition

CAPITAL = 100_000.0


def _t(strategy, pnl, day=2):
    return {"strategy": strategy, "pnl": pnl,
            "entry_date": date(2026, 6, 1), "exit_date": date(2026, 6, day)}


# ── the predicate ────────────────────────────────────────────────────────

def test_adopted_is_recognised_as_a_mapping_or_an_object():
    class Row:
        strategy = ADOPTED_STRATEGY

    assert is_adopted({"strategy": ADOPTED_STRATEGY})
    assert is_adopted(Row())
    assert not is_adopted({"strategy": "equity"})


def test_a_missing_or_null_strategy_is_not_adopted():
    """Fail towards counting a row, not towards hiding it.

    A row with no strategy is a gap in the data, and silently dropping it
    from performance would be the same class of error this module fixes.
    """
    assert not is_adopted({})
    assert not is_adopted({"strategy": None})


def test_partition_returns_both_halves():
    a, d = partition([_t("equity", 10), _t(ADOPTED_STRATEGY, -99), _t(None, 5)])
    assert len(a) == 2 and len(d) == 1


# ── the number that was wrong, and the regression that must not return ──

def test_an_adopted_loss_is_in_the_headline_total():
    """Production's exact shape: a positive strategy and a larger adopted
    loss. The headline is the account's result, so it is negative."""
    trades = [_t("equity", 3163.51)] + [_t(ADOPTED_STRATEGY, -4263.07)]

    out = compute_performance(trades, CAPITAL)

    assert out["total_pnl"] == -1099.56, (
        f"total_pnl={out['total_pnl']} — an adopted position's P&L is real "
        f"money off the broker's own cost basis; dropping it reports a "
        f"profit on a losing book"
    )
    assert out["total_trades"] == 2
    assert out["attributable_pnl"] == 3163.51
    assert out["adopted_pnl"] == -4263.07


def test_the_headline_is_never_just_the_attributable_half():
    """The specific regression: excluding adopted rows from the total.

    Asserted as an inequality rather than a value so it keeps biting if the
    numbers change — any book where the two halves have opposite signs must
    report a headline different from its attributable half.
    """
    out = compute_performance(
        [_t("equity", 500), _t(ADOPTED_STRATEGY, -900)], CAPITAL)

    assert out["attributable_pnl"] == 500.0
    assert out["total_pnl"] == -400.0
    assert out["total_pnl"] != out["attributable_pnl"]
    assert out["total_pnl"] == out["attributable_pnl"] + out["adopted_pnl"]


def test_the_split_is_reported_not_silent():
    out = compute_performance(
        [_t("equity", 100), _t(ADOPTED_STRATEGY, -500)], CAPITAL)

    assert out["adopted_positions"] == 1
    assert out["adopted_pnl"] == -500.0
    assert out["attributable_trades"] == 1
    assert out["attributable_pnl"] == 100.0
    assert out["adopted_present"] is True


def test_a_book_with_no_adopted_rows_says_so():
    out = compute_performance([_t("equity", 100)], CAPITAL)
    assert out["adopted_positions"] == 0
    assert out["adopted_pnl"] == 0.0
    assert out["adopted_present"] is False
    assert out["attributable_pnl"] == out["total_pnl"]


def test_win_rate_and_expectancy_cover_the_whole_account():
    """Account-level statistics include inherited positions: the losses were
    taken, whoever chose them."""
    trades = [_t("equity", 100), _t("equity", 300)] + [
        _t(ADOPTED_STRATEGY, -1000) for _ in range(3)]

    out = compute_performance(trades, CAPITAL)

    assert out["total_trades"] == 5
    assert out["win_rate"] == 0.4
    assert out["losses"] == 3
    assert out["total_pnl"] == -2600.0
    assert out["attributable_pnl"] == 400.0
    assert out["adopted_positions"] == 3


def test_an_all_adopted_book_reports_its_real_loss():
    """A book of nothing but inherited positions still lost the money.

    The first version returned total_pnl 0.0 here, which is the worst case of
    the bug: a 20,000 loss rendered as a flat desk with nothing to see.
    """
    out = compute_performance([_t(ADOPTED_STRATEGY, -5000) for _ in range(4)],
                              CAPITAL)

    assert out["total_trades"] == 4
    assert out["total_pnl"] == -20000.0
    assert out["adopted_positions"] == 4
    assert out["adopted_pnl"] == -20000.0
    assert out["attributable_trades"] == 0
    assert out["attributable_pnl"] == 0.0
    assert out["adopted_present"] is True


# ── callers that predate this must be unaffected ─────────────────────────

def test_a_caller_that_passes_no_strategy_is_unchanged():
    """Several callers build rows with only pnl and dates. They must keep
    working, and nothing of theirs may be misread as adopted."""
    rows = [{"pnl": 100.0, "entry_date": date(2026, 6, 1),
             "exit_date": date(2026, 6, 2)},
            {"pnl": -40.0, "entry_date": date(2026, 6, 1),
             "exit_date": date(2026, 6, 3)}]

    out = compute_performance(rows, CAPITAL)

    assert out["total_trades"] == 2
    assert out["total_pnl"] == 60.0
    assert out["adopted_positions"] == 0
    assert out["attributable_pnl"] == 60.0


def test_unmeasured_disclosure_still_works_alongside_this_one():
    """The two disclosures are different and must both be visible: a trade
    with no exit price is unmeasured; an adopted trade is unattributable but
    still counted."""
    out = compute_performance(
        [_t("equity", 100),
         {"strategy": "equity", "pnl": None, "entry_date": date(2026, 6, 1)},
         _t(ADOPTED_STRATEGY, -500)],
        CAPITAL)

    assert out["unmeasured_trades"] == 1
    assert out["performance_incomplete"] is True
    assert out["adopted_positions"] == 1
    assert out["total_trades"] == 2
    assert out["total_pnl"] == -400.0


# ── the boundary: risk must keep counting them ───────────────────────────

def test_risk_paths_still_count_adopted_positions():
    """An adopted position is real money at the broker.

    Excluding it from exposure, heat, concurrent-position limits or
    reconciliation would reintroduce exactly the blindness the adopt path was
    built to fix — an untracked position invisible to every DB-derived
    guardrail. Performance is the only place the distinction belongs, so this
    asserts the risk modules never learned about it.
    """
    import inspect

    from app.services import guardrails, portfolio_engine, position_reconciler

    for mod in (guardrails, portfolio_engine, position_reconciler):
        src = inspect.getsource(mod)
        assert "trade_attribution" not in src and ADOPTED_STRATEGY not in src, (
            f"{mod.__name__} now filters adopted positions. They are real "
            f"capital at the broker — read this test's docstring before "
            f"changing it."
        )
