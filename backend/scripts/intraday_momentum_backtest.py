"""Candidate E — intraday momentum: the early session predicts the last half hour.

Read-only research. No broker, no orders, no database. Reads the same 5-minute
regular-hours CSV as vwap_reversion_backtest.py (made by intraday_bars_fetch.py).

    python scripts/intraday_momentum_backtest.py bars.csv [--symbols QQQ SPY]

The idea (Gao, Han, Li & Zhou, "Market Intraday Momentum", J. Financial
Economics 2018, on SPY and other ETFs): the return of the first half hour,
measured from the previous close, and the return of the 12th half hour
(15:00-15:30) predict the return of the last half hour. The published effect is
small — a predictive R-squared of about 1.6% — and a rule that trades it earned
roughly 6% a year GROSS on SPY in the paper's sample. That is on the order of
0.03% a day, not the 0.3-0.5% target. This script exists to find out whether
anything survives costs on QQQ/SPY in recent data; it does not assume it does.

One trade a day per symbol, entered at the OPEN of the 15:30 bar (the bar after
the signal bar completes) and closed at the 15:55 close, so all of the
conservative conventions of vwap_reversion_backtest.py apply: next-bar entry,
slippage on both sides, commission on both sides, stop checked before anything
else with a gap filling at the open.

Signals (all use only data completed by 15:30, and the regression uses only
PRIOR sessions):

    r1       sign of the first-half-hour return
    r12      sign of the 15:00-15:30 return
    both     trade only when r1 and r12 agree
    ols      rolling OLS on past sessions: last-30-min return ~ r1 + r12
    always_long / always_short   BASELINES. A signal that does not beat simply
                                 holding the same 25 minutes every day has found
                                 the drift, not a predictor.

A result is evidence about the past under these assumptions. It is not a
forecast and it is not approval to trade.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, replace
from itertools import product
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vwap_reversion_backtest as v   # noqa: E402  (load_bars, daily_pnl, daily_summary, EXPOSURE_CAPS)

# end-of-bar minutes (ET) of the anchors
E_1000, E_1500, E_1530, E_ENTRY_FIRST, E_EXIT = 600, 900, 930, 935, 955
E_PREV_CLOSE = 960
HOLD_END_MINS = (935, 940, 945, 950, 955)     # the five bars held: 15:30 .. 15:50 starts
MIN_OLS_OBS = 60
MAX_GAP_DAYS = 4                              # a longer gap to the previous session: skip the day

SIGNALS = ("r1", "r12", "both", "ols", "always_long", "always_short")


@dataclass(frozen=True)
class Params:
    signal: str = "r1"
    thr_bps: float = 0.0              # minimum |signal| in basis points to trade
    stop_pct: Optional[float] = None  # percent of entry; None = no stop
    ols_window: int = 250
    slippage: float = 0.01            # $/share/side
    commission: float = 0.005         # $/share/side
    cost_mult: float = 1.0


# ── daily anchors ───────────────────────────────────────────────────────────

def build_daily(bars: pd.DataFrame) -> pd.DataFrame:
    """One row per symbol-session with the anchors; sessions missing any are dropped."""
    rows, skipped = [], 0
    for sym, g in bars.groupby("symbol", sort=True):
        prev_close, prev_date = None, None
        for d, day in g.groupby("date", sort=True):
            by_end = day.set_index("end_min")
            need = (E_1000, E_1500, E_1530, *HOLD_END_MINS)
            have_all = all(m in by_end.index for m in need)
            close_now = by_end["close"].get(E_PREV_CLOSE) if E_PREV_CLOSE in by_end.index else None
            ok = have_all and prev_close is not None and \
                (pd.Timestamp(d) - pd.Timestamp(prev_date)).days <= MAX_GAP_DAYS
            if ok:
                h = by_end.loc[list(HOLD_END_MINS)]
                rows.append({
                    "symbol": sym, "date": d, "prev_close": prev_close,
                    "r1": by_end.at[E_1000, "close"] / prev_close - 1.0,
                    "r12": by_end.at[E_1530, "close"] / by_end.at[E_1500, "close"] - 1.0,
                    "o": h["open"].to_numpy(float), "h": h["high"].to_numpy(float),
                    "l": h["low"].to_numpy(float), "c": h["close"].to_numpy(float),
                })
            else:
                skipped += 1
            prev_close, prev_date = (close_now if close_now is not None else None), d
    out = pd.DataFrame(rows)
    if not out.empty:
        out["target"] = out.apply(lambda r: r["c"][-1] / r["o"][0] - 1.0, axis=1)
        out = out.sort_values(["symbol", "date"]).reset_index(drop=True)
    out.attrs["skipped_sessions"] = skipped        # set last: sorting returns a new frame
    return out


def ols_forecast(daily: pd.DataFrame, window: int) -> np.ndarray:
    """Forecast of the last-30-min return for each row from sessions STRICTLY BEFORE it.

    Per symbol, refit on the previous `window` sessions each day. NaN until
    MIN_OLS_OBS sessions exist. Never sees the day it predicts.
    """
    out = np.full(len(daily), np.nan)
    for sym, idx in daily.groupby("symbol", sort=False).indices.items():
        idx = np.asarray(idx)
        r1, r12, y = (daily[k].to_numpy(float)[idx] for k in ("r1", "r12", "target"))
        for t in range(len(idx)):
            lo = max(0, t - window)
            if t - lo < MIN_OLS_OBS:
                continue
            X = np.column_stack([np.ones(t - lo), r1[lo:t], r12[lo:t]])
            beta, *_ = np.linalg.lstsq(X, y[lo:t], rcond=None)
            out[idx[t]] = beta[0] + beta[1] * r1[t] + beta[2] * r12[t]
    return out


def sides_for(daily: pd.DataFrame, p: Params, ols: Optional[np.ndarray] = None) -> np.ndarray:
    thr = p.thr_bps / 1e4
    r1, r12 = daily["r1"].to_numpy(float), daily["r12"].to_numpy(float)
    if p.signal == "always_long":
        return np.ones(len(daily), int)
    if p.signal == "always_short":
        return -np.ones(len(daily), int)
    if p.signal == "r1":
        raw, ok = r1, np.abs(r1) >= thr
    elif p.signal == "r12":
        raw, ok = r12, np.abs(r12) >= thr
    elif p.signal == "both":
        raw = r1
        ok = (np.sign(r1) == np.sign(r12)) & (np.abs(r1) >= thr) & (np.abs(r12) >= thr)
    elif p.signal == "ols":
        if ols is None:
            ols = ols_forecast(daily, p.ols_window)
        raw, ok = ols, np.isfinite(ols) & (np.abs(np.nan_to_num(ols)) >= thr)
    else:
        raise ValueError(f"unknown signal {p.signal!r}")
    return np.where(ok & np.isfinite(raw), np.sign(raw), 0).astype(int)


# ── trades ──────────────────────────────────────────────────────────────────

def simulate(daily: pd.DataFrame, p: Params, ols: Optional[np.ndarray] = None) -> pd.DataFrame:
    cols = ["symbol", "date", "side", "entry", "exit", "net_pct", "reason"]
    if daily.empty:
        return pd.DataFrame([], columns=cols)
    slip, comm = p.slippage * p.cost_mult, p.commission * p.cost_mult
    sides = sides_for(daily, p, ols)
    rows = []
    for i, side in enumerate(sides):
        if side == 0:
            continue
        r = daily.iloc[i]
        o, h, l, c = r["o"], r["h"], r["l"], r["c"]
        entry = o[0] + side * slip
        stop = None if p.stop_pct is None else entry * (1 - side * p.stop_pct / 100)
        exit_px, reason = c[-1] - side * slip, "close"
        if stop is not None:
            for k in range(len(o)):
                if side == 1:
                    if o[k] <= stop:
                        exit_px, reason = o[k] - slip, "stop_gap"; break
                    if l[k] <= stop:
                        exit_px, reason = stop - slip, "stop"; break
                else:
                    if o[k] >= stop:
                        exit_px, reason = o[k] + slip, "stop_gap"; break
                    if h[k] >= stop:
                        exit_px, reason = stop + slip, "stop"; break
        per_share = (exit_px - entry) * side - 2 * comm
        rows.append((r["symbol"], r["date"], "long" if side == 1 else "short", entry, exit_px,
                     per_share / entry * 100.0, reason))
    return pd.DataFrame(rows, columns=cols)


def summarize(t: pd.DataFrame, seed: int = 7, boots: int = 2000) -> dict:
    if t.empty:
        return {"n": 0}
    x = t["net_pct"].to_numpy(float)
    se = x.std(ddof=1) / np.sqrt(len(x)) if len(x) > 1 else float("nan")
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(boots, len(x)), replace=True).mean(axis=1)
    w, lo = x[x > 0], x[x <= 0]
    return {
        "n": int(len(x)), "win_rate": float((x > 0).mean()), "mean_pct": float(x.mean()),
        "t_stat": float(x.mean() / se) if se and np.isfinite(se) and se > 0 else float("nan"),
        "ci95": (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))),
        "profit_factor": float(w.sum() / -lo.sum()) if lo.sum() < 0 else float("inf"),
    }


# ── grid with a chronological holdout ───────────────────────────────────────

GRID = {
    "signal": ("r1", "r12", "both", "ols"),
    "thr_bps": (0.0, 5.0, 10.0),
    "stop_pct": (None, 0.5),
    "ols_window": (120, 250),
}
BASELINES = ("always_long", "always_short")


def _cells(grid: dict = GRID) -> list[Params]:
    seen, out = set(), []
    for vals in product(*grid.values()):
        p = replace(Params(), **dict(zip(grid, vals)))
        if p.signal != "ols":
            p = replace(p, ols_window=Params().ols_window)   # window is meaningless off ols
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def run_grid(daily: pd.DataFrame, grid: dict = GRID, split: float = 0.6, min_trades: int = 60,
             caps: tuple = v.EXPOSURE_CAPS) -> dict:
    dates = sorted(daily["date"].unique()) if not daily.empty else []
    if len(dates) < 30:
        return {"error": f"only {len(dates)} usable sessions; need at least 30"}
    cut = dates[int(len(dates) * split)]
    ols_cache = {w: ols_forecast(daily, w) for w in {c.ols_window for c in _cells(grid)}}
    early_mask = (daily["date"] < cut).to_numpy()

    def run(p: Params, mask: np.ndarray) -> pd.DataFrame:
        o = ols_cache.get(p.ols_window) if p.signal == "ols" else None
        sub = daily[mask]
        return simulate(sub, p, None if o is None else o[mask])

    cells = _cells(grid)
    scored = []
    for p in cells:
        s = summarize(run(p, early_mask))
        if s["n"] >= min_trades:
            scored.append((s["mean_pct"], p, s))
    if not scored:
        return {"error": "no cell reached the minimum trade count on the selection period",
                "cells_tried": len(cells)}
    scored.sort(key=lambda r: r[0], reverse=True)
    _, best, sel = scored[0]
    late_mask = ~early_mask
    late_t = run(best, late_mask)
    late_dates = sorted(daily.loc[late_mask, "date"].unique())
    n_sym = max(daily["symbol"].nunique(), 1)
    by_cap = {}
    for cap in caps:
        t = late_t.assign(eq_pct=late_t["net_pct"] * cap / n_sym)
        by_cap[cap] = v.daily_summary(v.daily_pnl(t, late_dates))
    base = {b: summarize(run(replace(Params(), signal=b), late_mask)) for b in BASELINES}
    return {
        "cells_tried": len(cells), "selection_days": len(dates) - len(late_dates),
        "holdout_days": len(late_dates), "best": best, "selection": sel,
        "holdout": summarize(late_t),
        "holdout_2x_costs": summarize(run(replace(best, cost_mult=2.0), late_mask)),
        "baselines": base, "holdout_daily_by_cap": by_cap,
        "skipped_sessions": daily.attrs.get("skipped_sessions", 0),
    }


def format_report(res: dict) -> str:
    if "error" in res:
        return f"NOT ENOUGH DATA: {res['error']}"
    b, s, h, h2, base = res["best"], res["selection"], res["holdout"], res["holdout_2x_costs"], res["baselines"]
    best_base = max(base[k].get("mean_pct", float("-inf")) for k in base)
    if h.get("n", 0) == 0 or h["mean_pct"] <= 0:
        verdict = "Holdout mean is NOT positive after costs. Do not proceed."
    elif h["mean_pct"] <= best_base:
        verdict = ("Holdout is positive but does NOT beat simply holding the same 25 minutes every day "
                   "(a baseline). That is drift, not a predictor.")
    elif h["t_stat"] < 2.0:
        verdict = "Holdout beats the baselines but the t-statistic is below 2: indistinguishable from luck."
    else:
        verdict = ("Holdout is positive, beats the baselines and has t>=2 at 1x costs. Check the 2x-cost "
                   "line and the sample size before reading anything into it; one holdout is not proof.")
    fmt = lambda d: (f"n={d.get('n', 0)} win {d.get('win_rate', 0):.1%} mean {d.get('mean_pct', 0):+.4f}% "
                     f"t={d.get('t_stat', float('nan')):+.2f}")
    return "\n".join([
        "Candidate E — intraday momentum (first half hour / 15:00-15:30 -> last 25 minutes), QQQ/SPY shares",
        f"cells tried: {res['cells_tried']}   sessions skipped for missing anchors: {res['skipped_sessions']}",
        f"selection days: {res['selection_days']}   holdout days: {res['holdout_days']}",
        f"chosen on selection: signal={b.signal} thr={b.thr_bps}bp stop={b.stop_pct} ols_window={b.ols_window}",
        f"selection : {fmt(s)}",
        f"HOLDOUT   : {fmt(h)}  95% CI [{h['ci95'][0]:+.4f}, {h['ci95'][1]:+.4f}]  PF {h['profit_factor']:.2f}"
        if h.get("n", 0) else "HOLDOUT   : no trades",
        f"HOLDOUT 2x costs: {fmt(h2)}",
        f"baseline hold every day  long: {fmt(base['always_long'])}",
        f"baseline hold every day short: {fmt(base['always_short'])}",
        "holdout daily result, % of equity, by exposure cap (notional / equity, split across symbols):",
        *[
            f"  {cap:>4.2f}x: mean {d['mean_daily_pct']:+.4f} [{d['mean_ci95'][0]:+.4f}, {d['mean_ci95'][1]:+.4f}]  "
            f">=0.3%: {d['days_ge_0.3']:.0%}  green {d['green_days']:.0%}  "
            f"worst {d['worst_day_pct']:+.2f}%  maxDD {d['max_drawdown_pct']:.2f}%"
            for cap, d in res["holdout_daily_by_cap"].items()
        ],
        verdict,
        "The published effect is ~0.03% a day gross; do not expect the 0.3-0.5% target from this signal.",
    ])


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("bars_csv")
    ap.add_argument("--symbols", nargs="*", default=["QQQ", "SPY"])
    args = ap.parse_args(argv)
    df = pd.read_csv(args.bars_csv)
    df = df[df["symbol"].isin(args.symbols)]
    print(format_report(run_grid(build_daily(v.load_bars(df)))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
