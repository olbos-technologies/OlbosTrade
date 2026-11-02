"""The research backtest must be conservative and must not look ahead.

These tests pin the MECHANICS of scripts/vwap_reversion_backtest.py on bars made
up for the purpose. They say nothing about whether the strategy makes money —
synthetic prices cannot — only that when real bars are run through it, a result
is not flattered by the simulator.

The property that matters most is that every ambiguity resolves AGAINST the
strategy: stop before target inside one bar, gap-through fills at the open,
entry on the next bar, features from completed bars only.
"""
import importlib.util
import pathlib
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "vwap_reversion_backtest.py"


def _load():
    spec = importlib.util.spec_from_file_location("vwap_reversion_backtest", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    import sys
    sys.modules["vwap_reversion_backtest"] = m      # dataclasses needs it registered
    spec.loader.exec_module(m)
    return m


bt = _load()
P = bt.Params()


def test_the_script_is_actually_read():
    assert SCRIPT.exists() and "def simulate" in SCRIPT.read_text()


# ── helpers ─────────────────────────────────────────────────────────────────

def _bars(n_days=6, seed=1, symbols=("QQQ",), drift=0.0):
    rows = []
    days = pd.bdate_range("2025-03-03", periods=n_days)
    for si, sym in enumerate(symbols):
        rng = np.random.default_rng(seed + si)
        px = 400.0
        for d in days:
            for k in range(78):
                ts = pd.Timestamp(d) + pd.Timedelta(hours=9, minutes=30 + 5 * k)
                o = px * (1 + rng.normal(0, 0.0002))      # opens gap from the prior close
                c = o * (1 + rng.normal(drift, 0.0007))
                hi, lo = max(o, c) * (1 + abs(rng.normal(0, 0.0003))), min(o, c) * (1 - abs(rng.normal(0, 0.0003)))
                rows.append((sym, ts.isoformat(), o, hi, lo, c, int(rng.integers(100_000, 200_000))))
                px = c
    return pd.DataFrame(rows, columns=["symbol", "ts", "open", "high", "low", "close", "volume"])


def _path(opens, highs, lows, closes, start_end_min=10 * 60):
    """Arrays + end timestamps for _run_trade, one 5-minute bar each."""
    n = len(opens)
    base = pd.Timestamp("2025-03-03 00:00", tz=bt.TZ) + pd.Timedelta(minutes=start_end_min)
    end_ts = [base + pd.Timedelta(minutes=5 * i) for i in range(n)]
    end_min = np.array([t.hour * 60 + t.minute for t in end_ts])
    f = lambda a: np.array(a, float)
    return f(opens), f(highs), f(lows), f(closes), end_min, end_ts


def _flat(n, px=100.0):
    return [px] * n


# ── exits: every ambiguity resolves against the strategy ────────────────────

def test_stop_wins_when_one_bar_touches_both_stop_and_target():
    # entry 100.01 -> stop 99.73, target 100.41; the bar spans both.
    o, h, l, c, em, et = _path([100, 100], [100.6, 100], [99.6, 100], [100, 100])
    k, px, reason, entry = bt._run_trade(o, h, l, c, em, et, 0, 1, P)
    assert reason == "stop" and px < entry


def test_a_bar_that_opens_beyond_the_stop_fills_at_the_open():
    o, h, l, c, em, et = _path([100, 99.0], [100.1, 99.2], [99.9, 98.9], [100, 99.1])
    k, px, reason, entry = bt._run_trade(o, h, l, c, em, et, 0, 1, P)
    assert reason == "stop_gap" and k == 1
    assert px == pytest.approx(99.0 - P.slippage)      # not the 99.73 stop price


def test_touching_the_target_is_not_a_fill_but_trading_through_it_is():
    entry = 100.0 + P.slippage
    target = entry * (1 + P.target_pct / 100)
    for high, expect in ((target, "flatten"), (target + P.tick, "target")):
        n = 4
        # after the touch the breakeven stop is armed (~100.02), so later bars
        # stay above it and the only way out is the 15:55 flatten.
        o, h, l, c, em, et = _path([100.0, 100.3, 100.3, 100.3], [100.0, high, 100.3, 100.3],
                                   [99.95, 100.25, 100.25, 100.25], [100.3] * n,
                                   start_end_min=15 * 60 + 40)
        _, _, reason, _ = bt._run_trade(o, h, l, c, em, et, 0, 1, P)
        assert reason == expect, (high, reason)


def test_short_stop_wins_a_both_touch_and_target_needs_a_tick_through():
    o, h, l, c, em, et = _path([100, 100], [100.6, 100], [99.4, 100], [100, 100])
    _, px, reason, entry = bt._run_trade(o, h, l, c, em, et, 0, -1, P)
    assert reason == "stop" and px > entry              # a short stop buys higher


def test_time_stop_fires_when_the_holding_period_elapses():
    n = 30
    o, h, l, c, em, et = _path(_flat(n), [100.05] * n, [99.95] * n, _flat(n), start_end_min=10 * 60)
    k, _, reason, _ = bt._run_trade(o, h, l, c, em, et, 0, 1, replace(P, time_stop_min=75))
    assert reason == "time" and k == 14                 # bar 14 ends 75 minutes after entry


def test_everything_is_flat_by_1555():
    n = 10
    o, h, l, c, em, et = _path(_flat(n), [100.05] * n, [99.95] * n, _flat(n), start_end_min=15 * 60 + 30)
    k, _, reason, _ = bt._run_trade(o, h, l, c, em, et, 0, 1, replace(P, time_stop_min=600))
    assert reason == "flatten" and em[k] >= bt.FLATTEN_MIN


def test_breakeven_stop_arms_after_the_trigger_and_covers_fees():
    entry = 100.0 + P.slippage
    trig = entry * (1 + P.be_trigger_pct / 100)
    # bar 0 reaches the trigger (below target); bar 1 dips back to just above entry.
    o, h, l, c, em, et = _path([100, 100.1, 100], [trig + 0.01, 100.1, 100], [99.95, 99.99, 100],
                               [100.1, 100.05, 100])
    k, px, reason, e = bt._run_trade(o, h, l, c, em, et, 0, 1, P)
    assert reason == "stop" and k == 1
    assert px == pytest.approx(e + 2 * P.commission - P.slippage)


def test_doubling_costs_makes_the_same_trade_worse():
    n = 4
    arrs = _path(_flat(n), [100.0, 100.6, 100, 100], [99.95] * n, _flat(n), start_end_min=15 * 60 + 40)
    one = bt._run_trade(*arrs, 0, 1, P)
    two = bt._run_trade(*arrs, 0, 1, replace(P, cost_mult=2.0))
    assert two[3] > one[3]                              # dearer entry


# ── features: no look-ahead ─────────────────────────────────────────────────

def _feats(df):
    return bt.prepare(bt.load_bars(df))


def test_vwap_resets_each_session_and_z_waits_for_enough_bars():
    f = _feats(_bars(3))
    first_of_day = f.groupby("date").head(1)
    tp = (first_of_day["high"] + first_of_day["low"] + first_of_day["close"]) / 3
    assert np.allclose(first_of_day["vwap"], tp)
    assert f.groupby("date").head(bt.Z_MIN_BARS - 1)["z"].isna().all()


def test_features_at_time_t_do_not_depend_on_later_bars():
    df = _bars(7)
    base = _feats(df)
    cut = len(df) // 2 + 5
    mutated = df.copy()
    mutated.loc[cut + 1:, ["open", "high", "low", "close"]] *= 1.5
    mutated.loc[cut + 1:, "volume"] *= 3
    after = _feats(mutated)
    cols = ["vwap", "dev", "z", "atr_ratio", "vol_ratio", "h_slope"]
    a, b = base.iloc[: cut + 1][cols], after.iloc[: cut + 1][cols]
    pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True))


def test_hourly_trend_changes_only_after_the_hour_it_describes_has_ended():
    df = _bars(7)
    base = _feats(df)
    d = base["date"].iloc[len(base) // 2]
    day_idx = base.index[base["date"] == d]
    # An hourly bar's close is its LAST five-minute bar. Change bar 23 (the
    # close of hour 2): bars 12..22 are still inside that hour and must not see
    # it; bar 23 itself ends exactly when the hour does, so it may.
    m = df.copy()
    m_day = m.index[pd.to_datetime(m["ts"]).dt.date == d]
    m.loc[m_day[23], "close"] *= 1.2
    after = _feats(m)
    inside = day_idx[12:23]
    assert np.allclose(base.loc[inside, "h_slope"].fillna(0), after.loc[inside, "h_slope"].fillna(0))
    assert not np.allclose(base.loc[day_idx[23:30], "h_slope"].fillna(0),
                           after.loc[day_idx[23:30], "h_slope"].fillna(0))


def test_a_signal_enters_on_the_next_bars_open_not_the_signal_bars_close():
    f = _feats(_bars(8, seed=3))
    t = bt.simulate(f, replace(P, z_entry=1.0, trend_filter="none", windows=((585, 930),)))
    assert len(t) > 0
    nxt_open = f.set_index(["symbol", "ts"])["open"]
    for r in t.head(20).itertuples():
        raw = nxt_open[(r.symbol, r.entry_ts)]
        want = raw + (P.slippage if r.side == "long" else -P.slippage)
        assert r.entry == pytest.approx(want)


# ── daily rules ─────────────────────────────────────────────────────────────

def _t(rows):
    ts = lambda m: pd.Timestamp("2025-03-03 00:00", tz=bt.TZ) + pd.Timedelta(minutes=m)
    return pd.DataFrame(
        [(pd.Timestamp("2025-03-03").date(), ts(a), ts(b), pct) for a, b, pct in rows],
        columns=["date", "entry_ts", "exit_ts", "net_pct"])


def test_daily_loss_limit_stops_later_entries():
    t = _t([(600, 610, -1.0), (620, 630, -1.0), (640, 650, 1.0)])
    out = bt.apply_daily_rules(t, P, daily_loss_limit=1.5)
    assert len(out) == 2                                # third skipped: realised -2.0


def test_the_loss_limit_only_counts_trades_already_exited():
    t = _t([(600, 700, -1.0), (610, 620, -1.0), (630, 640, 1.0)])
    out = bt.apply_daily_rules(t, P, daily_loss_limit=1.5, max_concurrent=3)
    assert len(out) == 3                                # first still open at the 2nd/3rd entry


def test_concurrency_and_trade_caps_apply():
    t = _t([(600, 700, 0.1), (601, 700, 0.1), (602, 700, 0.1)])
    assert len(bt.apply_daily_rules(t, P, max_concurrent=2)) == 2
    t2 = _t([(600 + 20 * i, 610 + 20 * i, 0.1) for i in range(5)])
    assert len(bt.apply_daily_rules(t2, P, max_trades=3, daily_profit_goal=None)) == 3


def test_profit_goal_stops_entries():
    t = _t([(600, 610, 1.0), (620, 630, 1.0)])
    out = bt.apply_daily_rules(t, P, daily_profit_goal=0.6, max_notional_x=1.0)
    assert len(out) == 1


def test_notional_cap_decides_equity_return():
    t = _t([(600, 610, 0.40)])
    capped = bt.apply_daily_rules(t, P, risk_pct=0.5, max_notional_x=0.3)
    free = bt.apply_daily_rules(t, P, risk_pct=0.5, max_notional_x=5.0)
    assert capped["eq_pct"].iloc[0] == pytest.approx(0.3 * 0.40)
    assert free["eq_pct"].iloc[0] == pytest.approx(0.5 / P.stop_pct * 0.40)


# ── whole-pipeline behaviour on awkward inputs ──────────────────────────────

def test_empty_and_tiny_inputs_do_not_crash():
    f = _feats(_bars(1))
    assert bt.simulate(f.iloc[0:0], P).empty
    assert bt.summarize_trades(pd.DataFrame()) == {"n": 0}
    out = bt.run_grid(f)
    assert "error" in out and "NOT ENOUGH DATA" in bt.format_report(out)


def test_missing_columns_are_rejected():
    with pytest.raises(ValueError, match="missing columns"):
        bt.load_bars(pd.DataFrame({"symbol": ["QQQ"]}))


def test_grid_reports_cell_count_and_judges_on_the_late_period(tmp_path):
    f = _feats(_bars(30, seed=5, symbols=("QQQ", "SPY")))
    small = {"z_entry": (1.5, 1.9), "stop_pct": (0.22, 0.28), "target_pct": (0.3,),
             "time_stop_min": (60,), "trend_filter": ("none",)}
    res = bt.run_grid(f, small, min_trades=1)
    assert res["cells_tried"] == 4
    assert res["selection_days"] + res["holdout_days"] == 30
    assert res["holdout_days"] == 12
    text = bt.format_report(res)
    assert "cells tried: 4" in text and "HOLDOUT" in text
    assert set(res["holdout_daily_by_cap"]) == set(bt.EXPOSURE_CAPS)
    assert "0.08x" in text and "2.00x" in text


def test_cli_end_to_end(tmp_path, capsys):
    p = tmp_path / "bars.csv"
    _bars(14, symbols=("QQQ", "SPY", "IWM")).to_csv(p, index=False)
    assert bt.main([str(p), "--symbols", "QQQ", "SPY"]) == 0
    out = capsys.readouterr().out
    assert "Candidate D" in out and "cells tried: 108" in out


def test_the_default_grid_is_the_one_in_the_preregistration():
    doc = (pathlib.Path(__file__).resolve().parents[2] / "docs" / "research"
           / "0DTE_STRATEGY_BANK.md").read_text()
    assert "Candidate D" in doc
    assert len(bt._cells(bt.GRID)) == 108


def _grid_res(sel_r, hold_r, hold2_r):
    stat = lambda r: {"n": 500, "win_rate": 0.45, "expectancy_r": r, "profit_factor": 1.0}
    cap = {"mean_daily_pct": 0.0, "mean_ci95": (-0.1, 0.1), "mean_without_best5_pct": 0.0,
           "days_ge_0.3": 0.0, "days_ge_0.5": 0.0, "green_days": 0.4, "worst_day_pct": -1.0,
           "max_drawdown_pct": -5.0}
    return {"cells_tried": 108, "selection_days": 500, "holdout_days": 300,
            "best": bt.Params(), "selection": stat(sel_r), "holdout": stat(hold_r),
            "holdout_2x_costs": stat(hold2_r),
            "holdout_daily_by_cap": {c: cap for c in bt.EXPOSURE_CAPS}}


def test_d_verdict_does_not_call_noise_an_edge():
    r = lambda *a: bt.format_report(_grid_res(*a))
    assert "NOT positive" in r(0.05, -0.01, -0.02)
    # the real 2026 result: best-of-108 LOST in selection, holdout +0.009R -> noise
    assert "No cell was profitable even on the selection period" in r(-0.021, 0.009, -0.007)
    assert "not at 2x" in r(0.05, 0.02, -0.01)
    assert "positive at 1x and 2x costs" in r(0.05, 0.02, 0.01)
