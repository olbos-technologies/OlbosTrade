"""Candidate E must not look ahead, must cost what it says, and must be able to FIND an effect.

A backtest that reports "nothing here" is only informative if it would have
reported "something" had there been something. So alongside the mechanics there
is a power test: plant a known momentum effect in synthetic bars and require the
pipeline to detect it, and require a pure random walk NOT to be called a
discovery. Synthetic data cannot say whether QQQ/SPY has an edge — only whether
the machinery is honest.
"""
import pathlib
import sys

import numpy as np
import pandas as pd
import pytest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts"))
import test_vwap_reversion_backtest as base   # noqa: E402  (random-walk generator)
import intraday_momentum_backtest as m        # noqa: E402

v = m.v
P = m.Params()

# bar index k starts at 09:30 + 5k minutes
K_1000, K_1500, K_1530, K_ENTRY, K_EXIT = 5, 65, 71, 72, 76


def _flat_days(levels, overrides=None):
    """Every bar of day d sits at levels[d] (o=h=l=c), except explicit overrides."""
    overrides = overrides or {}
    rows = []
    days = pd.bdate_range("2025-03-03", periods=len(levels))
    for di, d in enumerate(days):
        for k in range(78):
            ts = pd.Timestamp(d) + pd.Timedelta(hours=9, minutes=30 + 5 * k)
            o, h, l, c = overrides.get((di, k), (levels[di],) * 4)
            rows.append(("QQQ", ts.isoformat(), o, h, l, c, 100_000))
    return pd.DataFrame(rows, columns=["symbol", "ts", "open", "high", "low", "close", "volume"])


def _daily(levels, overrides=None):
    return m.build_daily(v.load_bars(_flat_days(levels, overrides)))


def _planted(n_days, beta, seed, symbols=("QQQ", "SPY")):
    """Random-walk bars where the last 25 minutes drift with beta * (first-half-hour return)."""
    rows = []
    days = pd.bdate_range("2023-01-02", periods=n_days)
    for si, sym in enumerate(symbols):
        rng = np.random.default_rng(seed + si)
        prev_close = 400.0
        for d in days:
            px, r1 = prev_close * (1 + rng.normal(0, 0.002)), 0.0
            for k in range(78):
                ts = pd.Timestamp(d) + pd.Timedelta(hours=9, minutes=30 + 5 * k)
                o = px
                c = o * (1 + rng.normal(0, 0.0007))
                if K_ENTRY <= k <= K_EXIT:
                    c *= 1 + beta * r1 / 5.0
                hi, lo = max(o, c) * 1.0002, min(o, c) * 0.9998
                rows.append((sym, ts.isoformat(), o, hi, lo, c, 150_000))
                px = c
                if k == K_1000:
                    r1 = c / prev_close - 1.0
            prev_close = px
    return pd.DataFrame(rows, columns=["symbol", "ts", "open", "high", "low", "close", "volume"])


# ── anchors ─────────────────────────────────────────────────────────────────

def test_anchors_use_the_right_bars_and_the_first_session_has_no_signal():
    over = {(1, K_1000): (110, 110, 110, 110), (1, K_1500): (100, 100, 100, 100),
            (1, K_1530): (101, 101, 101, 101),
            (1, K_ENTRY): (101.5, 102, 101, 101.8), (1, K_EXIT): (102, 103, 102, 102.5)}
    d = _daily([100.0, 100.0, 100.0], over)
    assert len(d) == 2                                   # day 0 has no previous close
    r = d.iloc[0]
    assert r["prev_close"] == 100.0
    assert r["r1"] == pytest.approx(110 / 100 - 1)        # 10:00 close vs previous close
    assert r["r12"] == pytest.approx(101 / 100 - 1)       # 15:30 close vs 15:00 close
    assert r["o"][0] == 101.5 and r["c"][-1] == 102.5 and len(r["o"]) == 5
    assert r["target"] == pytest.approx(102.5 / 101.5 - 1)


def test_a_session_missing_an_anchor_is_skipped_and_counted():
    df = _flat_days([100.0] * 4)
    df = df[~((pd.to_datetime(df["ts"]).dt.date == pd.Timestamp("2025-03-05").date()) &
              (pd.to_datetime(df["ts"]).dt.strftime("%H:%M") == "15:50"))]
    d = m.build_daily(v.load_bars(df))
    assert pd.Timestamp("2025-03-05").date() not in set(d["date"])
    assert d.attrs["skipped_sessions"] >= 2              # day 0 (no prev close) + the broken day


def test_a_long_gap_to_the_previous_session_is_skipped():
    df = _flat_days([100.0] * 3)
    df["ts"] = [(pd.Timestamp(t) + pd.Timedelta(days=10 * (pd.Timestamp(t).date() > pd.Timestamp("2025-03-04").date()))).isoformat()
                for t in df["ts"]]
    d = m.build_daily(v.load_bars(df))
    assert len(d) == 1                                    # the day after the 10-day hole is dropped


# ── no look-ahead ───────────────────────────────────────────────────────────

def test_signals_do_not_depend_on_anything_after_1530():
    base_df = _planted(60, 0.0, seed=3, symbols=("QQQ",))
    a = m.build_daily(v.load_bars(base_df))
    target_day = sorted(a["date"])[30]
    ts = pd.to_datetime(base_df["ts"])
    late = ((ts.dt.hour * 60 + ts.dt.minute) >= 15 * 60 + 30) & (ts.dt.date == target_day)
    mut = base_df.copy()
    mut.loc[late, "close"] *= 1.07            # move only the closes: the outcome changes, the inputs must not
    b = m.build_daily(v.load_bars(mut))
    ra, rb = a[a["date"] == target_day].iloc[0], b[b["date"] == target_day].iloc[0]
    assert ra["r1"] == rb["r1"] and ra["r12"] == rb["r12"]       # that day's signals are untouched
    assert not np.isclose(ra["target"], rb["target"])             # its outcome does move
    nxt = sorted(a["date"])[31]                                   # tomorrow's r1 legitimately uses today's close
    assert a[a["date"] == nxt].iloc[0]["r1"] != b[b["date"] == nxt].iloc[0]["r1"]


def test_the_regression_never_sees_the_day_it_predicts_or_any_later_day():
    d = m.build_daily(v.load_bars(_planted(120, 0.5, seed=4, symbols=("QQQ",))))
    f0 = m.ols_forecast(d, 60)
    t = 100
    # Manual fit on exactly the 60 prior sessions must equal the module's forecast.
    X = np.column_stack([np.ones(60), d["r1"][t - 60:t], d["r12"][t - 60:t]])
    beta, *_ = np.linalg.lstsq(X, d["target"][t - 60:t].to_numpy(float), rcond=None)
    assert f0[t] == pytest.approx(beta[0] + beta[1] * d["r1"][t] + beta[2] * d["r12"][t])
    # Change the target of day t and every later day: forecasts up to and including t are unchanged.
    d2 = d.copy()
    d2.loc[t:, "target"] = d2.loc[t:, "target"] * -3 + 0.01
    f1 = m.ols_forecast(d2, 60)
    assert np.allclose(f0[:t + 1], f1[:t + 1], equal_nan=True)
    assert not np.allclose(f0[t + 1:], f1[t + 1:], equal_nan=True)


def test_no_forecast_until_enough_history_exists():
    d = m.build_daily(v.load_bars(_planted(90, 0.5, seed=5, symbols=("QQQ",))))
    f = m.ols_forecast(d, 250)
    assert np.isnan(f[:m.MIN_OLS_OBS]).all() and np.isfinite(f[m.MIN_OLS_OBS:]).all()


# ── signals ─────────────────────────────────────────────────────────────────

def _sig_frame(r1, r12):
    return pd.DataFrame({"r1": r1, "r12": r12})


def test_signal_rules_and_thresholds():
    d = _sig_frame([0.002, -0.002, 0.0004, 0.002], [0.001, 0.001, 0.001, -0.001])
    s = lambda **kw: list(m.sides_for(d, m.Params(**kw)))
    assert s(signal="r1") == [1, -1, 1, 1]
    assert s(signal="r1", thr_bps=5) == [1, -1, 0, 1]     # 0.0004 = 4bp is below 5bp
    assert s(signal="r12") == [1, 1, 1, -1]
    assert s(signal="both") == [1, 0, 1, 0]               # only where r1 and r12 agree
    assert s(signal="always_long") == [1] * 4 and s(signal="always_short") == [-1] * 4
    with pytest.raises(ValueError, match="unknown signal"):
        s(signal="astrology")


# ── trade mechanics ─────────────────────────────────────────────────────────

def _one_day(over=None, r1_up=True):
    lv = [100.0, 100.0]
    ov = {(1, K_1000): (101, 101, 101, 101)} if r1_up else {(1, K_1000): (99, 99, 99, 99)}
    ov.update(over or {})
    return _daily(lv, ov)


def test_entry_is_the_next_bars_open_exit_is_the_1555_close_and_costs_are_charged():
    d = _one_day({(1, K_ENTRY): (100, 100.2, 99.9, 100.1), (1, K_ENTRY + 1): (100.4, 100.5, 100.3, 100.4),
                   (1, K_EXIT): (100.3, 100.6, 100.2, 100.5)})
    t = m.simulate(d, P)
    assert len(t) == 1 and t["side"][0] == "long" and t["reason"][0] == "close"
    assert t["entry"][0] == pytest.approx(100.0 + P.slippage)
    assert t["exit"][0] == pytest.approx(100.5 - P.slippage)
    gross = (100.5 - P.slippage) - (100.0 + P.slippage) - 2 * P.commission
    assert t["net_pct"][0] == pytest.approx(gross / (100.0 + P.slippage) * 100)
    t2 = m.simulate(d, m.Params(cost_mult=2.0))
    assert t2["net_pct"][0] < t["net_pct"][0]


def test_short_side_mirrors():
    d = _one_day({(1, K_ENTRY): (100, 100.1, 99.8, 99.9), (1, K_EXIT): (99.6, 99.7, 99.4, 99.5)}, r1_up=False)
    t = m.simulate(d, P)
    assert t["side"][0] == "short"
    assert t["entry"][0] == pytest.approx(100.0 - P.slippage)
    assert t["exit"][0] == pytest.approx(99.5 + P.slippage)
    assert t["net_pct"][0] > 0


def test_stop_fills_at_the_stop_and_a_gap_fills_at_the_open():
    p = m.Params(stop_pct=0.5)
    stop_px = (100.0 + P.slippage) * (1 - 0.005)
    d = _one_day({(1, K_ENTRY + 1): (100, 100, 99.0, 99.2)})
    t = m.simulate(d, p)
    assert t["reason"][0] == "stop" and t["exit"][0] == pytest.approx(stop_px - P.slippage)
    g = _one_day({(1, K_ENTRY + 1): (98.0, 98.5, 97.5, 98.0)})
    t = m.simulate(g, p)
    assert t["reason"][0] == "stop_gap" and t["exit"][0] == pytest.approx(98.0 - P.slippage)


# ── grid and honesty ────────────────────────────────────────────────────────

def test_grid_size_is_what_the_preregistration_says():
    cells = m._cells()
    assert len(cells) == 30                               # 6 + 6 + 6 + 12
    assert len({c for c in cells}) == 30
    assert sum(c.signal == "ols" for c in cells) == 12


def test_a_planted_effect_is_found_and_it_beats_the_baselines():
    d = m.build_daily(v.load_bars(_planted(500, 0.5, seed=11)))
    res = m.run_grid(d)
    h = res["holdout"]
    assert res["best"].signal in ("r1", "both", "ols")
    assert h["mean_pct"] > 0 and h["t_stat"] > 2.0
    assert h["mean_pct"] > max(b["mean_pct"] for b in res["baselines"].values())
    text = m.format_report(res)
    assert "Holdout is positive, beats the baselines" in text


@pytest.mark.parametrize("seed", [21, 22, 23])
def test_a_random_walk_is_not_called_a_discovery(seed):
    d = m.build_daily(v.load_bars(_planted(500, 0.0, seed=seed)))
    res = m.run_grid(d)
    text = m.format_report(res)
    assert "beats the baselines and has t>=2" not in text
    assert "Do not proceed" in text or "does NOT beat" in text or "below 2" in text


def test_too_little_data_and_the_cli(tmp_path, capsys):
    assert "NOT ENOUGH DATA" in m.format_report(m.run_grid(_daily([100.0] * 5)))
    assert "NOT ENOUGH DATA" in m.format_report(m.run_grid(pd.DataFrame()))
    csv = tmp_path / "bars.csv"
    _planted(120, 0.5, seed=2).to_csv(csv, index=False)
    assert m.main([str(csv), "--symbols", "QQQ", "SPY"]) == 0
    out = capsys.readouterr().out
    assert "Candidate E" in out and "cells tried: 30" in out and "baseline hold every day" in out


def _res(h_mean, h_t, base_long, base_short=-0.01):
    cap = {c: {"mean_daily_pct": 0.0, "mean_ci95": (0.0, 0.0), "days_ge_0.3": 0.0,
               "green_days": 0.5, "worst_day_pct": -0.1, "max_drawdown_pct": -0.5}
           for c in v.EXPOSURE_CAPS}
    stat = lambda mean, t: {"n": 200, "win_rate": 0.5, "mean_pct": mean, "t_stat": t,
                            "ci95": (mean - 0.01, mean + 0.01), "profit_factor": 1.1}
    return {"cells_tried": 30, "skipped_sessions": 0, "selection_days": 400, "holdout_days": 300,
            "best": P, "selection": stat(0.01, 1.0), "holdout": stat(h_mean, h_t),
            "holdout_2x_costs": stat(h_mean / 2, h_t / 2),
            "baselines": {"always_long": stat(base_long, 0.5), "always_short": stat(base_short, -0.5)},
            "holdout_daily_by_cap": cap}


def test_verdicts_separate_drift_from_luck_from_a_candidate():
    assert "Do not proceed" in m.format_report(_res(-0.001, -0.5, 0.0))
    assert "does NOT beat" in m.format_report(_res(0.01, 3.0, base_long=0.02))     # drift, not a predictor
    assert "below 2" in m.format_report(_res(0.01, 1.5, base_long=0.0))             # not distinguishable from luck
    assert "beats the baselines and has t>=2" in m.format_report(_res(0.01, 3.0, base_long=0.0))
