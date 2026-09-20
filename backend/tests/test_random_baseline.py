"""
The random-entry baseline, and the properties that make it mean anything.

A random baseline is unusually easy to ship broken: it produces numbers under
every bug, and the numbers look plausible because they are supposed to look
arbitrary. So these tests check the things that would silently make it a
worse-than-useless comparison rather than checking that it returns a float.

The three that matter, all of them load-bearing:

  TRADE COUNT AND HOLD DURATION ARE MATCHED to the reference. Unmatched, the
  comparison measures frequency or time-in-market instead of selection, and on
  a drifting tape those dominate everything else.

  PLACEMENT IS UNIFORM. "Pick a start, retry on collision" biases toward the
  middle of the window, which on a trending series is a systematically
  different sample than the edges.

  THE VERDICT FOLLOWS THE PERCENTILE, in the right direction. An inverted band
  here would report a signal that loses to chance as one that beats it, which
  is precisely the finding this exists to surface.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.baseline_comparison_engine import (
    STARTING_CAPITAL_DEFAULT, Trade, _random_entry_actions, _verdict,
    random_baseline_distribution, simulate_positions,
)


def _rng(seed=1):
    return np.random.default_rng(seed)


def _trade(hold_days: int, pnl: float = 0.0) -> Trade:
    return Trade(entry_date="2026-01-01", exit_date="2026-01-02",
                 entry_price=100.0, exit_price=100.0, pnl=pnl,
                 hold_days=hold_days)


# ── placement ───────────────────────────────────────────────────────────────

def test_positions_never_overlap():
    """The simulator is long/flat: a BUY while already long is silently
    dropped, so an overlapping layout would quietly produce FEWER trades than
    asked for and the count-matching above would be a lie."""
    actions = _random_entry_actions(200, [5, 5, 5, 5, 5], _rng())

    depth = 0
    for a in actions:
        if a == "BUY":
            depth += 1
        elif a == "SELL":
            depth -= 1
        assert depth in (0, 1), f"overlapping positions: depth reached {depth}"
    assert depth == 0, "a position was left open"


def test_the_requested_number_of_trades_is_placed():
    actions = _random_entry_actions(200, [3, 3, 3, 3], _rng())
    assert actions.count("BUY") == 4
    assert actions.count("SELL") == 4


def test_hold_durations_are_the_ones_requested():
    """Not just the count — the lengths. Random holding five days against a
    reference holding forty is a comparison of exposure, not of selection."""
    durations = [2, 7, 13]
    actions = _random_entry_actions(300, durations, _rng())

    held = []
    entry = None
    for i, a in enumerate(actions):
        if a == "BUY":
            entry = i
        elif a == "SELL" and entry is not None:
            held.append(i - entry)
            entry = None

    assert sorted(held) == sorted(durations)


def test_placement_is_not_biased_toward_the_middle():
    """A retry-on-collision implementation clusters entries centrally, which
    on a trending series samples a systematically different part of the tape.
    Over many draws the mean entry position should sit near the middle of the
    window, and — the part that actually detects clustering — entries should
    reach both ends.
    """
    n = 200
    rng = _rng(7)
    firsts, lasts = [], []
    for _ in range(400):
        actions = _random_entry_actions(n, [3], rng)
        idx = actions.index("BUY")
        firsts.append(idx)
        lasts.append(idx)

    assert min(firsts) < n * 0.1, "no entry ever landed in the first 10% of the window"
    assert max(lasts) > n * 0.85, "no entry ever landed in the last 15% of the window"
    assert 0.35 * n < float(np.mean(firsts)) < 0.65 * n


def test_trades_that_cannot_fit_are_dropped_not_overlapped():
    """Twenty 50-day holds do not fit in 60 bars. Squeezing them in would
    overlap; the honest answer is fewer trades."""
    actions = _random_entry_actions(60, [50] * 20, _rng())
    assert actions.count("BUY") <= 1


def test_an_empty_window_places_nothing():
    for n in (0, 1):
        assert set(_random_entry_actions(n, [3], _rng())) <= {"HOLD"}
    assert set(_random_entry_actions(100, [], _rng())) == {"HOLD"}


# ── the distribution ────────────────────────────────────────────────────────

def _flat_market(n=250):
    dates = [f"2026-01-{i % 28 + 1:02d}" for i in range(n)]
    closes = [100.0] * n
    return dates, closes


def _rising_market(n=250):
    dates = [f"2026-01-{i % 28 + 1:02d}" for i in range(n)]
    closes = [100.0 * (1.002 ** i) for i in range(n)]
    return dates, closes


def test_no_reference_trades_means_no_baseline():
    """A baseline matched to zero trades would compare against nothing while
    rendering as a real result."""
    dates, closes = _flat_market()
    assert random_baseline_distribution(dates, closes, []) is None


def test_the_baseline_is_calibrated_against_random_references():
    """A random reference must land UNIFORMLY among the random draws.

    This is the property that makes a percentile mean anything: if the
    machinery were biased — entries clustered, durations drifting, returns
    computed against a different denominator — random references would pile
    up at one end and every real verdict would be shifted by the same amount.

    The first version of this test judged ONE random reference and asserted it
    landed mid-distribution. That is precisely the error this module's
    docstring warns about: a single draw is a coin flip, and it duly landed at
    the 4.5th percentile and failed. On a compounding tape a fixed-duration
    hold pays more in absolute terms the later it starts, so individual draws
    spread widely and legitimately. Many references, checked as a
    distribution, is the only form of this test that is not itself noise.
    """
    dates, closes = _rising_market()
    percentiles = []
    for seed in range(40):
        actions = _random_entry_actions(len(closes), [10] * 5, _rng(1000 + seed))
        ref_trades, _ = simulate_positions(dates, closes, actions)
        out = random_baseline_distribution(dates, closes, ref_trades,
                                           draws=60, seed=7 + seed)
        percentiles.append(out["percentile_of_random"])

    mean = float(np.mean(percentiles))
    assert 30.0 < mean < 70.0, (
        f"random references averaged the {mean:.0f}th percentile, not ~50 — "
        f"the baseline is biased: {sorted(percentiles)}")
    assert min(percentiles) < 25.0 and max(percentiles) > 75.0, (
        f"random references never spread across the range: {sorted(percentiles)}")


def test_random_entries_profit_on_a_rising_tape():
    """Sanity on the simulator underneath, kept separate from the calibration
    check above so a failure says which of the two broke."""
    dates, closes = _rising_market()
    actions = _random_entry_actions(len(closes), [10] * 5, _rng(99))
    ref_trades, _ = simulate_positions(dates, closes, actions)

    out = random_baseline_distribution(dates, closes, ref_trades, draws=200)

    assert out["random_return_mean_pct"] > 0
    assert "random" in out["verdict"]


def test_the_report_carries_the_spread_not_just_the_mean():
    """A mean alone cannot be argued with. The percentiles are what let a
    reader see whether the reference is inside the noise."""
    dates, closes = _rising_market()
    actions = _random_entry_actions(len(closes), [10] * 4, _rng(3))
    ref_trades, _ = simulate_positions(dates, closes, actions)

    out = random_baseline_distribution(dates, closes, ref_trades, draws=100)

    assert out["random_return_p05_pct"] <= out["random_return_p50_pct"] \
        <= out["random_return_p95_pct"]
    assert out["reference_trades"] == len(ref_trades)
    assert out["draws"] == 100


def test_the_result_is_reproducible():
    """A percentile nobody can reproduce is not a finding."""
    dates, closes = _rising_market()
    actions = _random_entry_actions(len(closes), [8] * 4, _rng(5))
    ref_trades, _ = simulate_positions(dates, closes, actions)

    a = random_baseline_distribution(dates, closes, ref_trades, draws=50, seed=42)
    b = random_baseline_distribution(dates, closes, ref_trades, draws=50, seed=42)
    c = random_baseline_distribution(dates, closes, ref_trades, draws=50, seed=43)

    assert a == b
    assert a["percentile_of_random"] != c["percentile_of_random"] or \
        a["random_return_mean_pct"] != c["random_return_mean_pct"], (
            "two different seeds produced identical results — the seed is "
            "not reaching the generator")


def test_random_draws_match_the_reference_trade_count():
    """The whole comparison rests on this. If the draws trade a different
    number of times, the percentile measures frequency."""
    dates, closes = _rising_market()
    actions = _random_entry_actions(len(closes), [6] * 7, _rng(11))
    ref_trades, _ = simulate_positions(dates, closes, actions)

    out = random_baseline_distribution(dates, closes, ref_trades, draws=100)

    assert out["random_trades_median"] == out["reference_trades"]


# ── the verdict ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pct,expect", [
    (99.0, "beats random"),
    (96.0, "beats random"),
    (80.0, "not conclusively"),
    (50.0, "indistinguishable"),
    (26.0, "indistinguishable"),
    (10.0, "worse than most"),
    (2.0, "inverted or harmful"),
    (0.0, "inverted or harmful"),
])
def test_the_verdict_follows_the_percentile(pct, expect):
    """An inverted band here would describe a signal that loses to chance as
    one that beats it — the exact finding this tool exists to surface."""
    assert expect in _verdict(pct)


def test_the_verdict_is_monotonic():
    """Reading down the bands must never get more favourable as the percentile
    falls. Checked as an ordering rather than band by band, so a future edit
    that swaps two thresholds cannot slip through."""
    rank = {
        "loses to random entries at the 5% level — the signal is inverted or harmful": 0,
        "worse than most random entries": 1,
        "indistinguishable from random entries": 2,
        "better than most random entries, not conclusively": 3,
        "beats random entries at the 5% level": 4,
    }
    seen = [rank[_verdict(p)] for p in range(0, 101, 1)]
    assert seen == sorted(seen), "verdict bands are not monotonic in percentile"


def test_a_tie_does_not_count_as_beating_random():
    """Strictly-less-than. A model that merely reproduces the draws should sit
    at the bottom, not be credited with beating them."""
    dates, closes = _flat_market()
    ref = [_trade(5, pnl=0.0) for _ in range(3)]

    out = random_baseline_distribution(dates, closes, ref, draws=50)

    assert out["reference_return_pct"] == 0.0
    assert out["percentile_of_random"] == 0.0
