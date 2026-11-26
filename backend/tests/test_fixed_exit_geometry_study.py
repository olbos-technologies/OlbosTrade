"""The fixed-exit-geometry research script: its arithmetic, and the simulator's conservative conventions on
synthetic bars with a known answer. No network and no broker."""
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_scripts = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_scripts))
_spec = importlib.util.spec_from_file_location("fixed_exit_geometry_study", _scripts / "fixed_exit_geometry_study.py")
fx = importlib.util.module_from_spec(_spec)
sys.modules["fixed_exit_geometry_study"] = fx
_spec.loader.exec_module(fx)
v = fx.v

A, B, C, D, E = fx.GEOMETRIES


def day_bars(date, path, symbol="SPY", wick=0.0):
    """5-minute bars 09:30 to 15:55 ET whose close follows `path` (one price per bar, 78 of them)."""
    n = 78
    assert len(path) == n
    ts = pd.date_range(f"{date} 09:30", periods=n, freq="5min")
    opens = np.concatenate([[path[0]], path[:-1]])
    rows = []
    for t, o, c in zip(ts, opens, path):
        rows.append((symbol, t.strftime("%Y-%m-%d %H:%M:%S"), o, max(o, c) + wick, min(o, c) - wick, c, 1000))
    return pd.DataFrame(rows, columns=["symbol", "ts", "open", "high", "low", "close", "volume"])


def bars_of(*frames):
    return v.load_bars(pd.concat(frames, ignore_index=True))


def drift(date, start, per_bar):
    return day_bars(date, [start * (1 + per_bar) ** i for i in range(78)])


# ── the arithmetic ───────────────────────────────────────────────────────────

def test_breakeven_for_the_proposal_is_two_in_three_before_costs():
    assert fx.breakeven_win_rate(0.5, 1.0) == pytest.approx(2 / 3)
    assert fx.breakeven_win_rate(0.5, 0.5) == pytest.approx(0.5)
    assert fx.breakeven_win_rate(0.5, 0.25) == pytest.approx(1 / 3)
    assert fx.breakeven_win_rate(1.0, 0.5) == pytest.approx(1 / 3)


def test_costs_raise_the_breakeven_win_rate():
    assert fx.breakeven_win_rate(0.5, 1.0, 0.05) == pytest.approx(1.05 / 1.5)
    assert fx.breakeven_win_rate(0.5, 1.0, 0.05) > fx.breakeven_win_rate(0.5, 1.0)


@pytest.mark.parametrize("args", [(0, 1, 0), (0.5, 0, 0), (-1, 1, 0), (0.5, 1, -0.1)])
def test_breakeven_rejects_nonsense(args):
    with pytest.raises(ValueError):
        fx.breakeven_win_rate(*args)


def test_a_driftless_walk_reaches_the_target_exactly_at_the_breakeven_rate():
    """With no edge the best a geometry can do IS its break-even rate: zero expectancy before costs."""
    for take, stop in [(0.5, 1.0), (0.5, 0.5), (1.0, 0.5)]:
        assert fx.driftless_win_rate(take, stop) == pytest.approx(fx.breakeven_win_rate(take, stop))


def test_hourly_atr_of_constant_range_bars():
    n = 20
    c = np.full(n, 100.0)
    h, l = c + 0.15, c - 0.15
    assert fx.hourly_atr_pct(h, l, c, 19) == pytest.approx(0.3 * np.sqrt(12) / 100 * 100)


def test_hourly_atr_is_none_without_enough_bars_or_on_flat_data():
    c = np.full(30, 100.0)
    assert fx.hourly_atr_pct(c, c, c, 5) is None                 # not enough bars yet
    assert fx.hourly_atr_pct(c, c, c, 25) is None                # enough bars but zero range: no ATR to use


def test_bootstrap_range_brackets_the_mean_and_is_nan_for_one_value():
    x = np.array([1.0, -1.0, 2.0, 0.5, -0.5] * 20)
    lo, hi = fx.bootstrap_mean_ci(x)
    assert lo < x.mean() < hi
    assert all(np.isnan(fx.bootstrap_mean_ci(np.array([1.0]))))


# ── the simulator on bars with a known answer ───────────────────────────────

def test_a_steady_rise_hits_the_target_every_time_and_pays_the_target_minus_costs():
    bars = bars_of(*[drift(f"2026-03-{d:02d}", 300, 0.0004) for d in range(2, 12)])
    t = fx.run(bars, A, "grid")
    assert len(t) > 30
    assert set(t["reason"]) == {"target"}
    assert t["net_pct"].mean() == pytest.approx(0.5 - 2 * 0.005 / 300 * 100, abs=0.02)   # target less commission


def test_a_steady_fall_loses_about_the_stop_plus_costs_on_every_trade_that_reaches_it():
    bars = bars_of(*[drift(f"2026-03-{d:02d}", 300, -0.0004) for d in range(2, 12)])
    t = fx.run(bars, A, "grid")
    stopped = t[t["reason"].isin(["stop", "stop_gap"])]
    assert len(stopped) > 20
    assert set(t["reason"]) <= {"stop", "stop_gap", "flatten"}      # late entries run out of day before the stop
    assert stopped["net_pct"].mean() < -1.0                         # the stop (1%) plus slippage and commission
    assert stopped["net_pct"].mean() > -1.1
    assert (t["net_pct"] < 0).all()                                 # a falling market never pays a long


def test_a_gap_through_the_stop_fills_at_the_open_not_at_the_stop():
    df = day_bars("2026-03-02", [300.0] * 78)
    df.loc[20:, ["open", "high", "low", "close"]] = 291.0           # bar 20 OPENS 3% lower and never recovers
    t = fx.run(bars_of(df), A, "grid")
    gap = t[t["reason"] == "stop_gap"]
    assert len(gap) >= 1
    assert gap["net_pct"].min() < -2.5                              # the loss is the gap, not the 1% the stop promised


def test_nothing_is_held_past_the_flat_time():
    bars = bars_of(*[drift(f"2026-03-{d:02d}", 300, 0.00001) for d in range(2, 8)])
    t = fx.run(bars, A, "grid")
    assert len(t) > 0
    assert (pd.to_datetime(t["exit_ts"]).dt.hour * 60 + pd.to_datetime(t["exit_ts"]).dt.minute <= 15 * 60 + 55).all()
    assert (t["reason"] == "flatten").any()                         # an exit that is neither target nor stop


def test_every_geometry_sees_exactly_the_same_entries():
    rng = np.random.default_rng(3)
    frames = []
    for d in range(2, 12):
        path = 300 * np.exp(np.cumsum(rng.normal(0, 0.0008, 78)))
        frames.append(day_bars(f"2026-03-{d:02d}", list(path), wick=0.05))
    bars = bars_of(*frames)
    entries = {g.name: list(fx.run(bars, g, "grid")["entry_ts"]) for g in (A, B, C, D)}
    assert len(entries["A"]) > 30
    assert entries["A"] == entries["B"] == entries["C"] == entries["D"]


def test_the_adaptive_geometry_skips_entries_without_an_atr_instead_of_guessing():
    flat = bars_of(day_bars("2026-03-02", [300.0] * 78))           # zero range everywhere: no ATR
    assert fx.run(flat, E, "grid").empty


def test_opening_range_breakout_enters_once_a_day_and_only_after_a_complete_range():
    path = [300.0] * 78
    path[12:] = [301.5] * 66                                       # breaks above the 09:30-10:00 high after 10:00
    bars = bars_of(day_bars("2026-03-02", path, wick=0.1))
    t = fx.run(bars, A, "orb")
    assert len(t) == 1
    short_day = bars[bars["end_min"] <= 9 * 60 + 45]               # only the first 15 minutes: the range never completes
    assert fx.run(short_day, A, "orb").empty


def test_no_breakout_means_no_trade():
    assert fx.run(bars_of(day_bars("2026-03-02", [300.0] * 78, wick=0.1)), A, "orb").empty


# ── the summary and the report ──────────────────────────────────────────────

def test_summary_of_nothing_is_n_zero():
    assert fx.summarize(pd.DataFrame(), 10_000, 0.01) == {"n": 0}


def test_summary_reports_dollars_on_the_notional_and_the_breakeven_for_that_geometry():
    bars = bars_of(*[drift(f"2026-03-{d:02d}", 300, 0.0004) for d in range(2, 12)])
    s = fx.summarize(fx.run(bars, A, "grid"), 10_000, 0.01)
    assert s["win_rate"] == 1.0
    assert s["mean_dollars"] == pytest.approx(s["mean_pct"] / 100 * 10_000)
    assert s["breakeven_win"] == pytest.approx(fx.breakeven_win_rate(0.5, 1.0, 0.01))
    assert s["driftless_win"] == pytest.approx(2 / 3)


def test_a_small_sample_prints_insufficient_data_instead_of_a_mean():
    bars = bars_of(drift("2026-03-02", 300, 0.0004))
    results = {(e, g.name): fx.summarize(fx.run(bars, g, e), 10_000, 0.01) for e in ("grid", "orb") for g in fx.GEOMETRIES}
    text = fx.format_report(results, 10_000, ["SPY"], 1, 300.0)
    assert "INSUFFICIENT DATA" in text


def test_the_report_says_what_it_is_not_and_never_tells_anyone_to_trade():
    bars = bars_of(*[drift(f"2026-03-{d:02d}", 300, 0.0004) for d in range(2, 12)])
    results = {(e, g.name): fx.summarize(fx.run(bars, g, e), 10_000, 0.01) for e in ("grid", "orb") for g in fx.GEOMETRIES}
    text = fx.format_report(results, 10_000, ["SPY"], 10, 300.0)
    assert "not a forecast" in text and "Edge Lab" in text and "NULL entries" in text
    assert "0.5% / 1.0%" in text and "b/e%" in text
    assert "recommend" not in text.lower().replace("not as a recommendation", "")


def test_the_script_imports_nothing_that_can_reach_a_broker_or_the_database():
    import inspect
    lines = [ln for ln in inspect.getsource(fx).splitlines() if ln.startswith(("import ", "from "))]
    assert lines, "read no import lines: the guard would pass vacuously"
    for ln in lines:
        assert not any(w in ln for w in ("broker", "ib_insync", "app.", "sqlalchemy", "execution", "order")), ln


# ── gaps the first round of breaks found ────────────────────────────────────

def noisy_days(n=10, seed=5):
    rng = np.random.default_rng(seed)
    return bars_of(*[day_bars(f"2026-03-{d:02d}", list(300 * np.exp(np.cumsum(rng.normal(0, 0.0008, 78)))), wick=0.05)
                     for d in range(2, 2 + n)])


def test_an_opening_range_with_missing_bars_gives_no_entry_even_if_price_breaks_out_later():
    path = [300.0] * 78
    path[20:] = [302.0] * 58                                        # a clear breakout after 10:00
    df = day_bars("2026-03-02", path, wick=0.1)
    complete = fx.run(bars_of(df), A, "orb")
    assert len(complete) == 1                                       # with the full range the entry is taken
    damaged = bars_of(df.iloc[4:])                                  # the first four bars (09:30-09:50) are missing
    assert fx.run(damaged, A, "orb").empty


def test_the_adaptive_geometry_waits_for_a_full_atr_window_while_fixed_ones_enter_at_ten():
    bars = noisy_days()
    first = lambda g: min(pd.to_datetime(fx.run(bars, g, "grid")["entry_ts"]).dt.strftime("%H:%M"))
    assert first(A) == "10:00"
    assert first(E) >= "11:00"                                      # 14 bars of ATR history are needed first


def test_the_adaptive_geometry_keeps_its_one_to_one_and_a_half_ratio():
    t = fx.run(noisy_days(), E, "grid")
    assert len(t) > 30
    assert (t["stop_pct"] / t["take_pct"]).round(6).eq(1.5).all()   # stop is 1.5x ATR, target 1.0x: not swapped


def test_a_small_rise_does_not_move_the_stop_to_breakeven():
    """The fixed geometry's stop stays where it was set: a 0.3% rise that fades must still lose the full stop."""
    path = [300.0] * 11 + [300.9] * 4 + list(np.linspace(300.9, 292.0, 63))
    df = day_bars("2026-03-02", [float(x) for x in path])
    t = fx.run(bars_of(df), A, "grid")
    first = t.sort_values("entry_ts").iloc[0]                       # the 10:00 entry, before the rise
    assert first["reason"] in ("stop", "stop_gap")
    assert first["net_pct"] < -0.9
