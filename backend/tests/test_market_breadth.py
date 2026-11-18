"""Market breadth: the arithmetic, and every way the reading must refuse to look healthy.

Synthetic prices with a known answer: a rising name is always above its 50-day average, a
falling name always below, so the breadth of a mixed universe is known exactly.
"""
import numpy as np
import pandas as pd
import pytest

from app.services import market_breadth as mb

DATES = pd.bdate_range("2025-01-01", periods=120)


def universe(n_up, n_down, n=120):
    cols = {}
    for i in range(n_up):
        cols[f"UP{i}"] = np.linspace(100, 200, n)
    for i in range(n_down):
        cols[f"DN{i}"] = np.linspace(200, 100, n)
    return pd.DataFrame(cols, index=DATES[:n])


# ── the arithmetic ───────────────────────────────────────────────────────────

def test_all_rising_is_one_and_all_falling_is_zero():
    assert mb.breadth_table(universe(40, 0))["pct_above"].iloc[-1] == 1.0
    assert mb.breadth_table(universe(0, 40))["pct_above"].iloc[-1] == 0.0


def test_a_mixed_universe_has_the_exact_share():
    t = mb.breadth_table(universe(30, 10))
    assert t["pct_above"].iloc[-1] == pytest.approx(0.75)
    assert t["n_valid"].iloc[-1] == 40 and t["n_universe"].iloc[-1] == 40


def test_no_value_until_a_name_has_fifty_closes():
    t = mb.breadth_table(universe(5, 5))
    assert t["pct_above"].iloc[:mb.MA_WINDOW - 1].isna().all()
    assert (t["n_valid"].iloc[:mb.MA_WINDOW - 1] == 0).all()
    assert t["n_valid"].iloc[mb.MA_WINDOW - 1] == 10


def test_a_missing_close_is_not_counted_as_below():
    c = universe(20, 0)
    c.iloc[-1, :10] = np.nan                       # half the names have no close today
    t = mb.breadth_table(c)
    assert t["n_valid"].iloc[-1] == 10
    assert t["pct_above"].iloc[-1] == 1.0          # the ten that reported are all above; absent is not "below"


def test_zero_and_negative_closes_are_bad_data_and_ignored():
    c = universe(10, 0)
    c.iloc[-1, 0] = 0.0
    c.iloc[-1, 1] = -5.0
    t = mb.breadth_table(c)
    assert t["n_valid"].iloc[-1] == 8


def test_non_numeric_cells_are_ignored():
    c = universe(10, 0).astype(object)
    c.iloc[-1, 0] = "n/a"
    assert mb.breadth_table(c)["n_valid"].iloc[-1] == 9


def test_rows_out_of_order_are_sorted():
    c = universe(20, 0).iloc[::-1]
    assert mb.breadth_table(c)["pct_above"].iloc[-1] == 1.0


@pytest.mark.parametrize("bad", [None, pd.DataFrame(), [1, 2, 3]])
def test_empty_or_wrong_input_gives_an_empty_table(bad):
    t = mb.breadth_table(bad)
    assert t.empty and list(t.columns) == ["pct_above", "n_valid", "n_universe"]


def test_lines_are_empty_until_a_full_window():
    t = mb.add_lines(mb.breadth_table(universe(20, 20)))
    assert t["fast"].first_valid_index() == DATES[mb.MA_WINDOW - 1 + mb.FAST_WINDOW - 1]
    assert t["slow"].isna().all()                  # only 120 bars of data: no 200-day line, and no fake one


def test_the_slow_line_exists_with_enough_history():
    n = 330
    dates = pd.bdate_range("2024-01-01", periods=n)
    c = pd.DataFrame({f"U{i}": np.linspace(100, 300, n) for i in range(40)}, index=dates)
    t = mb.add_lines(mb.breadth_table(c))
    assert t["slow"].iloc[-1] == pytest.approx(1.0)
    assert t["slow"].first_valid_index() == dates[mb.MA_WINDOW - 1 + mb.SLOW_WINDOW - 1]


# ── the reading ──────────────────────────────────────────────────────────────

def reading_table(fast, n_valid=100, n_universe=100, slow=0.6):
    idx = pd.bdate_range("2026-01-01", periods=3)
    return pd.DataFrame({"pct_above": [fast] * 3, "n_valid": [n_valid] * 3, "n_universe": [n_universe] * 3,
                         "fast": [fast] * 3, "slow": [slow] * 3}, index=idx)


@pytest.mark.parametrize("fast,state", [
    (0.10, mb.WASHOUT), (0.399, mb.WASHOUT), (0.40, mb.WEAK), (0.499, mb.WEAK),
    (0.50, mb.HEALTHY), (0.699, mb.HEALTHY), (0.70, mb.EXTENDED), (0.95, mb.EXTENDED),
])
def test_states_at_their_boundaries(fast, state):
    r = mb.classify(reading_table(fast))
    assert r.state == state and r.reasons and r.n_valid == 100


def test_too_few_names_is_unknown_not_healthy():
    r = mb.classify(reading_table(0.6, n_valid=12, n_universe=12))
    assert r.state == mb.UNKNOWN and "only 12 names" in r.reasons[0]


def test_a_feed_that_lost_names_is_unknown():
    r = mb.classify(reading_table(0.6, n_valid=60, n_universe=100))
    assert r.state == mb.UNKNOWN and "missing" in r.reasons[0]


def test_coverage_at_the_limit_is_accepted():
    assert mb.classify(reading_table(0.6, n_valid=80, n_universe=100)).state == mb.HEALTHY


def test_no_history_for_the_fast_line_is_unknown():
    t = reading_table(0.6)
    t["fast"] = float("nan")
    assert mb.classify(t).state == mb.UNKNOWN


@pytest.mark.parametrize("bad", [None, pd.DataFrame(), pd.DataFrame({"pct_above": [0.5]})])
def test_no_table_is_unknown(bad):
    assert mb.classify(bad).state == mb.UNKNOWN


def test_missing_slow_line_is_said_not_hidden():
    r = mb.classify(reading_table(0.6, slow=float("nan")))
    assert r.slow is None and any("200-day" in x for x in r.reasons)


def test_end_to_end_on_a_collapsing_market():
    n = 120
    cols = {f"S{i}": np.concatenate([np.linspace(100, 160, 70), np.linspace(160, 90, 50)]) for i in range(40)}
    t, r = mb.summarise(pd.DataFrame(cols, index=DATES[:n]))
    assert r.state == mb.WASHOUT and r.as_of == DATES[n - 1]
    assert r.pct_above == 0.0 and r.n_universe == 40


# ── turning points ───────────────────────────────────────────────────────────

def wave(n=200, period=60, amp=0.3, mid=0.5):
    x = np.arange(n)
    return pd.Series(mid + amp * np.sin(2 * np.pi * x / period), index=pd.bdate_range("2025-01-01", periods=n))


def test_finds_the_peaks_and_troughs_of_a_wave():
    pts = mb.turning_points(wave(), window=15, prominence=0.2)
    kinds = [p.kind for p in pts]
    assert kinds[:4] == ["peak", "trough", "peak", "trough"]       # alternating, as a sine does
    assert all(p.value > 0.7 for p in pts if p.kind == "peak")
    assert all(p.value < 0.3 for p in pts if p.kind == "trough")


def test_the_newest_bars_never_carry_a_turning_point():
    s = wave(n=150, period=60)
    # the wave's last peak sits at bar ~135 (x=15+60+60): with window 20 it cannot be confirmed yet
    pts = mb.turning_points(s, window=20, prominence=0.2)
    assert all(s.index.get_loc(p.date) < len(s) - 20 for p in pts)


def test_a_small_wiggle_is_not_a_turning_point():
    s = wave(amp=0.03)                                              # swings of 0.06 against a 0.10 bar
    assert mb.turning_points(s, window=10, prominence=0.10) == []


def test_a_plateau_is_reported_once():
    vals = [0.3] * 20 + [0.8] * 5 + [0.3] * 20
    s = pd.Series(vals, index=pd.bdate_range("2025-01-01", periods=len(vals)))
    peaks = [p for p in mb.turning_points(s, window=8, prominence=0.2) if p.kind == "peak"]
    assert len(peaks) == 1


def test_too_short_a_series_has_none():
    assert mb.turning_points(wave(n=15), window=10) == []
    assert mb.turning_points(pd.Series(dtype=float)) == []
    assert mb.turning_points(wave(), window=0) == []


def test_gaps_in_the_series_are_skipped():
    s = wave()
    s.iloc[::7] = np.nan
    assert mb.turning_points(s, window=15, prominence=0.2)          # still finds them, no crash


# ── the module stays pure ────────────────────────────────────────────────────

def test_the_module_imports_nothing_that_can_reach_a_broker_or_the_network():
    import inspect
    lines = [ln for ln in inspect.getsource(mb).splitlines() if ln.startswith(("import ", "from "))]
    assert lines, "read no import lines: the guard would pass vacuously"
    for ln in lines:
        assert not any(w in ln for w in ("broker", "execution", "order", "yfinance", "requests", "httpx", "app.")), ln
