"""Adopted positions are kept out of strategy performance, and said so.

The failure this prevents was measured on production (2026-10-17, three
months of paper trading):

    strategy            n    total P&L    win %
    equity             43    +3,163.51     53.5
    adopted_untracked  25    -4,263.07     48.0
    ------------------------------------------
    combined           68    -1,099.56     51.5

The desk concluded it was losing money. The strategy was positive. 37% of the
rows were positions the reconciler adopted from the broker, whose entry price,
strikes and entry date are discovery-time placeholders — `main`'s own adopt
path logs them as "placeholders, not real historical entry data". P&L against
an invented basis measures how far a position moved since somebody noticed it.

So this is not cosmetic: on the real book it inverted the sign of the number
the whole operation is steered by.

Two properties, and the second matters as much as the first:

1. Adopted rows are excluded from win rate, expectancy and total P&L.
2. They are REPORTED, not dropped. The repo already works this way —
   `unmeasured_trades` travels with performance, `heat_overstated` with
   portfolio heat — because silently excluding 25 of 68 rows replaces one
   misleading number with another.

What is NOT tested here, deliberately, because it must not change: adopted
positions still count for exposure, heat, concurrent-position limits and
reconciliation. The capital is real. `trade_attribution` is imported by none
of those paths, and `test_risk_paths_still_count_adopted_positions` pins that.
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


# ── the number that was wrong ────────────────────────────────────────────

def test_an_adopted_loss_does_not_flip_the_headline():
    """Production's exact shape, scaled down: a positive strategy and a
    larger adopted loss. Before this, the sum was reported as the result."""
    trades = [_t("equity", 3163.51)] + [_t(ADOPTED_STRATEGY, -4263.07)]

    out = compute_performance(trades, CAPITAL)

    assert out["total_pnl"] == 3163.51, (
        f"total_pnl={out['total_pnl']} — the adopted position's placeholder "
        f"basis is being counted as strategy performance"
    )
    assert out["total_trades"] == 1
    assert out["win_rate"] == 1.0


def test_the_exclusion_is_reported_not_silent():
    out = compute_performance(
        [_t("equity", 100), _t(ADOPTED_STRATEGY, -500)], CAPITAL)

    assert out["adopted_positions"] == 1
    assert out["adopted_pnl"] == -500.0
    assert out["adopted_excluded"] is True


def test_a_book_with_no_adopted_rows_says_so():
    out = compute_performance([_t("equity", 100)], CAPITAL)
    assert out["adopted_positions"] == 0
    assert out["adopted_excluded"] is False


def test_win_rate_and_expectancy_use_only_attributable_rows():
    # 2 strategy wins, and 3 adopted losses that must not drag the rate down.
    trades = [_t("equity", 100), _t("equity", 300)] + [
        _t(ADOPTED_STRATEGY, -1000) for _ in range(3)]

    out = compute_performance(trades, CAPITAL)

    assert out["win_rate"] == 1.0, f"win_rate={out['win_rate']}"
    assert out["expectancy"] == 200.0
    assert out["losses"] == 0
    assert out["adopted_positions"] == 3


def test_an_all_adopted_book_is_empty_performance_not_a_loss():
    """The edge that would otherwise divide by zero, and the one most likely
    to read as a catastrophic desk."""
    out = compute_performance([_t(ADOPTED_STRATEGY, -5000) for _ in range(4)],
                              CAPITAL)

    assert out["total_trades"] == 0
    assert out["total_pnl"] == 0.0
    assert out["adopted_positions"] == 4
    assert out["adopted_pnl"] == -20000.0
    assert out["adopted_excluded"] is True


# ── callers that predate this must be unaffected ─────────────────────────

def test_a_caller_that_passes_no_strategy_is_unchanged():
    """Several callers build rows with only pnl and dates. They must keep
    working, and nothing of theirs may be swallowed as adopted."""
    rows = [{"pnl": 100.0, "entry_date": date(2026, 6, 1),
             "exit_date": date(2026, 6, 2)},
            {"pnl": -40.0, "entry_date": date(2026, 6, 1),
             "exit_date": date(2026, 6, 3)}]

    out = compute_performance(rows, CAPITAL)

    assert out["total_trades"] == 2
    assert out["total_pnl"] == 60.0
    assert out["adopted_positions"] == 0


def test_unmeasured_disclosure_still_works_alongside_this_one():
    """The two exclusions are different and must both be visible: a trade with
    no exit price is unmeasured; an adopted trade is unattributable."""
    out = compute_performance(
        [_t("equity", 100),
         {"strategy": "equity", "pnl": None, "entry_date": date(2026, 6, 1)},
         _t(ADOPTED_STRATEGY, -500)],
        CAPITAL)

    assert out["unmeasured_trades"] == 1
    assert out["performance_incomplete"] is True
    assert out["adopted_positions"] == 1
    assert out["total_trades"] == 1


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
