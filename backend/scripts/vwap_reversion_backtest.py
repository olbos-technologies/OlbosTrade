"""Candidate D — intraday mean reversion to session VWAP on QQQ / SPY shares.

Read-only research. No broker, no orders, no database. It reads a CSV of 5-minute
regular-hours bars and reports what the rules in docs/research/0DTE_STRATEGY_BANK.md
would have done, with costs, a chronological holdout, and the number of
parameter cells tried.

    python scripts/vwap_reversion_backtest.py bars.csv [--symbols QQQ SPY]

CSV columns: symbol, ts, open, high, low, close, volume. `ts` is the bar START
(IBKR's convention), timezone-aware or America/New_York if naive.

Conventions, all deliberately conservative — each one makes the result worse,
never better, than a naive simulation:

* A signal is evaluated on a COMPLETED bar. The entry is the NEXT bar's OPEN,
  plus slippage, not the signal bar's close.
* Features use only bars that had ended by the signal bar's end. The hourly
  trend value is the last hourly bar that had COMPLETED at that time.
* If one bar touches both stop and target, the STOP is assumed to fill first.
* A bar that opens beyond the stop fills at the OPEN, not at the stop.
* A target must trade THROUGH by one tick; touching it is not a fill.
* Stops and time exits pay slippage; commissions are paid on both sides.

What this does NOT model: borrow availability or fees for shorts, partial
fills, halts, the order-book queue, or the broker's margin rules. It is a test
of whether the signal has an edge, not a promise of what would be filled.

A result from this script is evidence about the past under these assumptions.
It is not a forecast and it is not approval to trade.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, replace
from itertools import product
from typing import Iterable, Optional

import numpy as np
import pandas as pd

BAR_MIN = 5
TZ = "America/New_York"
FLATTEN_MIN = 15 * 60 + 55          # flat by 15:55 ET
#: Signal-bar END windows: 09:45-11:30 and 13:30-15:30 ET. The lunch hour is
#: skipped outright (the source plan said "reduce or skip").
DEFAULT_WINDOWS = ((9 * 60 + 45, 11 * 60 + 30), (13 * 60 + 30, 15 * 60 + 30))
#: Bars of VWAP deviation needed before a z-score exists. The deviation's
#: standard deviation is unstable on a handful of points, so no signal fires
#: in the first 40 minutes — the earliest realistic entry is ~10:10, not the
#: 09:45 the source plan names.
Z_MIN_BARS = 8
Z_WINDOW = 48


@dataclass(frozen=True)
class Params:
    z_entry: float = 1.9
    stop_pct: float = 0.28            # percent of entry price
    target_pct: float = 0.40
    time_stop_min: int = 75
    trend_filter: str = "ema_slope"   # "ema_slope" | "none"
    atr_lo: float = 0.7
    atr_hi: float = 2.2
    vol_min: float = 0.6
    be_trigger_pct: float = 0.22      # move stop to breakeven + fees after this
    slippage: float = 0.01            # $/share/side
    commission: float = 0.005         # $/share/side
    cost_mult: float = 1.0
    tick: float = 0.01
    windows: tuple = DEFAULT_WINDOWS
    sides: tuple = ("long", "short")


# ── data ────────────────────────────────────────────────────────────────────

def load_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise to ET, sort, derive bar end, and drop anything outside RTH."""
    need = {"symbol", "ts", "open", "high", "low", "close", "volume"}
    missing = need - set(df.columns)
    if missing:
        raise ValueError(f"bars are missing columns: {sorted(missing)}")
    out = df.copy()
    ts = pd.to_datetime(out["ts"])
    out["ts"] = ts.dt.tz_localize(TZ) if ts.dt.tz is None else ts.dt.tz_convert(TZ)
    out = out.sort_values(["symbol", "ts"]).reset_index(drop=True)
    out["end"] = out["ts"] + pd.Timedelta(minutes=BAR_MIN)
    out["date"] = out["ts"].dt.date
    out["end_min"] = out["end"].dt.hour * 60 + out["end"].dt.minute
    start_min = out["ts"].dt.hour * 60 + out["ts"].dt.minute
    out = out[(start_min >= 9 * 60 + 30) & (out["end_min"] <= 16 * 60)]
    return out.drop_duplicates(["symbol", "ts"]).reset_index(drop=True)


def _features_one(g: pd.DataFrame) -> pd.DataFrame:
    g = g.copy()
    day = g.groupby("date", sort=False)
    tp = (g["high"] + g["low"] + g["close"]) / 3.0
    pv = (tp * g["volume"]).groupby(g["date"]).cumsum()
    vv = g["volume"].groupby(g["date"]).cumsum()
    g["vwap"] = pv / vv.replace(0, np.nan)
    g["dev"] = (g["close"] - g["vwap"]) / g["vwap"] * 100.0
    sig = day["dev"].transform(lambda s: s.rolling(Z_WINDOW, min_periods=Z_MIN_BARS).std())
    g["z"] = g["dev"] / sig.replace(0, np.nan)

    prev_close = day["close"].shift(1)
    tr = pd.concat([
        g["high"] - g["low"],
        (g["high"] - prev_close).abs(),
        (g["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(14, min_periods=14).mean()
    g["atr_ratio"] = atr / atr.rolling(20, min_periods=20).median()
    g["vol_ratio"] = g["volume"] / g["volume"].rolling(20, min_periods=20).mean()

    # Hourly trend from COMPLETED hourly bars only (12 five-minute bars).
    pos = day.cumcount()
    grp = pos // 12
    hourly = g.assign(_grp=grp).groupby(["date", "_grp"], sort=False).agg(
        close=("close", "last"), end=("end", "last"), n=("close", "size"),
    )
    hourly = hourly[hourly["n"] == 12].reset_index(drop=True)
    hourly["slope"] = hourly["close"].ewm(span=20, adjust=False).mean().diff()
    hourly = hourly[["end", "slope"]].dropna().sort_values("end")
    # direction="backward" on `end`: a bar sees only hourly bars that had
    # already ended at or before its own end.
    g = pd.merge_asof(
        g.sort_values("end"), hourly.rename(columns={"slope": "h_slope"}),
        on="end", direction="backward",
    )
    return g.sort_values("ts").reset_index(drop=True)


def prepare(bars: pd.DataFrame) -> pd.DataFrame:
    """Features per symbol. Independent of strategy parameters, so computed once."""
    parts = [_features_one(g) for _, g in bars.groupby("symbol", sort=True)]
    return pd.concat(parts, ignore_index=True) if parts else bars.iloc[0:0]


# ── simulation ──────────────────────────────────────────────────────────────

def _signal(row, p: Params) -> int:
    """+1 long, -1 short, 0 none. Uses only values known at the bar's end."""
    z = row.z
    if not np.isfinite(z) or not np.isfinite(row.atr_ratio) or not np.isfinite(row.vol_ratio):
        return 0
    if not (p.atr_lo <= row.atr_ratio <= p.atr_hi) or row.vol_ratio < p.vol_min:
        return 0
    if not any(lo <= row.end_min <= hi for lo, hi in p.windows):
        return 0
    side = 1 if z <= -p.z_entry else (-1 if z >= p.z_entry else 0)
    if side == 1 and "long" not in p.sides:
        return 0
    if side == -1 and "short" not in p.sides:
        return 0
    if side != 0 and p.trend_filter == "ema_slope":
        slope = row.h_slope
        if not np.isfinite(slope) or (side == 1 and slope < 0) or (side == -1 and slope > 0):
            return 0
    return side


def _run_trade(o, h, l, c, end_min, end_ts, j, side, p: Params):
    slip = p.slippage * p.cost_mult
    comm = p.commission * p.cost_mult
    entry = o[j] + side * slip
    stop = entry * (1 - side * p.stop_pct / 100)
    target = entry * (1 + side * p.target_pct / 100)
    be_level = entry * (1 + side * p.be_trigger_pct / 100)
    entry_ts = end_ts[j] - pd.Timedelta(minutes=BAR_MIN)
    be_done = False
    for k in range(j, len(o)):
        if side == 1:
            if o[k] <= stop:
                return k, o[k] - slip, "stop_gap", entry
            if l[k] <= stop:
                return k, stop - slip, "stop", entry
            if h[k] >= target + p.tick:
                return k, target, "target", entry
            if not be_done and h[k] >= be_level:
                be_done, stop = True, entry + 2 * comm
        else:
            if o[k] >= stop:
                return k, o[k] + slip, "stop_gap", entry
            if h[k] >= stop:
                return k, stop + slip, "stop", entry
            if l[k] <= target - p.tick:
                return k, target, "target", entry
            if not be_done and l[k] <= be_level:
                be_done, stop = True, entry - 2 * comm
        if end_min[k] >= FLATTEN_MIN:
            return k, c[k] - side * slip, "flatten", entry
        if (end_ts[k] - entry_ts) >= pd.Timedelta(minutes=p.time_stop_min):
            return k, c[k] - side * slip, "time", entry
    return len(o) - 1, c[-1] - side * slip, "flatten", entry


def simulate(feats: pd.DataFrame, p: Params) -> pd.DataFrame:
    """One position per symbol at a time. Returns one row per trade."""
    cols = ["symbol", "side", "entry_ts", "exit_ts", "entry", "exit",
            "net_pct", "r", "reason", "date"]
    rows = []
    comm = p.commission * p.cost_mult
    for (sym, _d), day in feats.groupby(["symbol", "date"], sort=False):
        day = day.reset_index(drop=True)
        o, h, l, c = (day[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        em, et = day["end_min"].to_numpy(), list(day["end"])
        rows_ = list(day[["z", "atr_ratio", "vol_ratio", "end_min", "h_slope"]].itertuples(index=False))
        i = 0
        while i < len(day) - 1:
            side = _signal(rows_[i], p)
            j = i + 1
            if side == 0 or em[j] >= FLATTEN_MIN:
                i += 1
                continue
            k, exit_px, reason, entry = _run_trade(o, h, l, c, em, et, j, side, p)
            per_share = (exit_px - entry) * side - 2 * comm
            net_pct = per_share / entry * 100.0
            rows.append((sym, "long" if side == 1 else "short",
                         et[j] - pd.Timedelta(minutes=BAR_MIN), et[k], entry, exit_px,
                         net_pct, net_pct / p.stop_pct, reason, day["date"].iloc[0]))
            i = k + 1
    return pd.DataFrame(rows, columns=cols)


# ── portfolio / daily rules ─────────────────────────────────────────────────

def apply_daily_rules(
    trades: pd.DataFrame, p: Params, *, risk_pct: float = 0.5,
    max_notional_x: float = 1.0, max_concurrent: int = 2, max_trades: int = 6,
    daily_loss_limit: float = 1.5, daily_profit_goal: Optional[float] = 0.6,
) -> pd.DataFrame:
    """Accept or skip trades in time order, then size them.

    A trade is skipped if, at its ENTRY time, the day's realised P&L (only trades
    already exited) is at/below the loss limit or at/above the profit goal, the
    trade cap is reached, or `max_concurrent` accepted trades are still open.
    Sizing: notional = risk_pct / stop_pct of equity, capped at `max_notional_x`.
    The cap usually binds — a 0.28% stop at 0.5% risk wants 1.8x equity — and
    that is the number that decides whether 0.3-0.5% a day is reachable at all.
    """
    if trades.empty:
        return trades.assign(eq_pct=pd.Series(dtype=float))
    x = min(risk_pct / p.stop_pct, max_notional_x)
    kept = []
    for _d, day in trades.sort_values("entry_ts").groupby("date", sort=False):
        accepted: list[tuple] = []          # (exit_ts, eq_pct)
        for t in day.itertuples():
            realised = sum(e for ex, e in accepted if ex <= t.entry_ts)
            still_open = sum(1 for ex, _e in accepted if ex > t.entry_ts)
            if realised <= -daily_loss_limit:
                continue
            if daily_profit_goal is not None and realised >= daily_profit_goal:
                continue
            if len(accepted) >= max_trades or still_open >= max_concurrent:
                continue
            eq = x * t.net_pct
            accepted.append((t.exit_ts, eq))
            kept.append((t.Index, eq))
    idx = [i for i, _ in kept]
    out = trades.loc[idx].copy()
    out["eq_pct"] = [e for _, e in kept]
    return out


# ── statistics ──────────────────────────────────────────────────────────────

def summarize_trades(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"n": 0}
    w, lo = t[t["net_pct"] > 0], t[t["net_pct"] <= 0]
    gross_w, gross_l = w["net_pct"].sum(), -lo["net_pct"].sum()
    return {
        "n": int(len(t)),
        "win_rate": float((t["net_pct"] > 0).mean()),
        "avg_win_pct": float(w["net_pct"].mean()) if len(w) else 0.0,
        "avg_loss_pct": float(lo["net_pct"].mean()) if len(lo) else 0.0,
        "expectancy_pct": float(t["net_pct"].mean()),
        "expectancy_r": float(t["r"].mean()),
        "profit_factor": float(gross_w / gross_l) if gross_l > 0 else float("inf"),
    }


def daily_pnl(t: pd.DataFrame, all_dates: Iterable) -> pd.Series:
    """Daily % of equity, with zero for every session that had no trade."""
    s = pd.Series(0.0, index=sorted(set(all_dates)))
    if not t.empty:
        s.loc[t.groupby("date")["eq_pct"].sum().index] = t.groupby("date")["eq_pct"].sum()
    return s


def daily_summary(d: pd.Series, seed: int = 7, boots: int = 2000) -> dict:
    if d.empty:
        return {"days": 0}
    rng = np.random.default_rng(seed)
    v = d.to_numpy(float)
    means = rng.choice(v, size=(boots, len(v)), replace=True).mean(axis=1)
    curve = np.cumprod(1 + v / 100.0)
    dd = float((curve / np.maximum.accumulate(curve) - 1).min() * 100.0)
    no_best5 = np.sort(v)[:-5].mean() if len(v) > 5 else float("nan")
    return {
        "days": int(len(v)), "mean_daily_pct": float(v.mean()),
        "mean_ci95": (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))),
        "mean_without_best5_pct": float(no_best5),
        "days_ge_0.3": float((v >= 0.3).mean()), "days_ge_0.5": float((v >= 0.5).mean()),
        "worst_day_pct": float(v.min()), "max_drawdown_pct": dd,
        "green_days": float((v > 0).mean()),
    }


# ── grid with a chronological holdout ───────────────────────────────────────

GRID = {
    "z_entry": (1.7, 1.9, 2.1),
    "stop_pct": (0.22, 0.28, 0.35),
    "target_pct": (0.30, 0.40, 0.50),
    "time_stop_min": (60, 90),
    "trend_filter": ("ema_slope", "none"),
}


def _cells(grid: dict) -> list[Params]:
    keys = list(grid)
    return [replace(Params(), **dict(zip(keys, vals))) for vals in product(*grid.values())]


#: Notional as a multiple of equity: 0.08 is the equity sizer's per-position
#: default (equity_signal_engine.max_position_pct), 0.30 the charter's exposure
#: cap, 1.0 fully invested cash, 2.0 a margin account. The same trades are
#: re-sized at each, because the cap — not the signal — usually decides whether
#: a percent-of-price edge can reach a percent-of-equity daily target.
EXPOSURE_CAPS = (0.08, 0.30, 1.0, 2.0)


def get_engine(name: str):
    """`python` is the reference. `cpp` is the accelerator, tested to match it exactly."""
    if name == "python":
        return simulate
    if name == "cpp":
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
        import vwap_reversion_cpp
        return vwap_reversion_cpp.simulate
    raise ValueError(f"unknown engine {name!r}; use 'python' or 'cpp'")


def run_grid(feats: pd.DataFrame, grid: dict = GRID, split: float = 0.6,
             min_trades: int = 30, caps: tuple = EXPOSURE_CAPS, engine: str = "python",
             **rules) -> dict:
    """Pick the best cell on the EARLY days; judge it only on the LATE days."""
    dates = sorted(feats["date"].unique())
    if len(dates) < 10:
        return {"error": f"only {len(dates)} sessions; need at least 10"}
    sim = get_engine(engine)
    cut = dates[int(len(dates) * split)]
    early, late = feats[feats["date"] < cut], feats[feats["date"] >= cut]
    cells = _cells(grid)
    scored = []
    for p in cells:
        s = summarize_trades(sim(early, p))
        if s["n"] >= min_trades:
            scored.append((s["expectancy_r"], p, s))
    if not scored:
        return {"error": "no cell reached the minimum trade count on the selection period",
                "cells_tried": len(cells)}
    scored.sort(key=lambda r: r[0], reverse=True)
    _, best, sel = scored[0]
    late_trades = sim(late, best)
    hold = summarize_trades(late_trades)
    late_dates = late["date"].unique()
    by_cap = {
        cap: daily_summary(daily_pnl(
            apply_daily_rules(late_trades, best, max_notional_x=cap, **rules), late_dates))
        for cap in caps
    }
    costs2 = summarize_trades(sim(late, replace(best, cost_mult=2.0)))
    return {
        "cells_tried": len(cells), "selection_days": int((early["date"].nunique())),
        "holdout_days": int(len(late_dates)), "best": best, "selection": sel,
        "holdout": hold, "holdout_2x_costs": costs2,
        "holdout_daily_by_cap": by_cap,
    }


def format_report(res: dict) -> str:
    if "error" in res:
        return f"NOT ENOUGH DATA: {res['error']}"
    b, s, h, h2 = res["best"], res["selection"], res["holdout"], res["holdout_2x_costs"]
    if h.get("n", 0) == 0 or h["expectancy_r"] <= 0:
        verdict = "Holdout expectancy is NOT positive. Do not proceed."
    elif s.get("expectancy_r", 0.0) <= 0:
        # The cell was picked as the BEST of N on the selection period. If even the best
        # cell lost money there, no cell worked, and a small holdout gain is noise.
        verdict = (f"No cell was profitable even on the selection period (the best of "
                   f"{res['cells_tried']} lost {s['expectancy_r']:+.3f}R per trade). A small "
                   f"positive holdout is noise, not an edge. Do not proceed.")
    elif h2.get("n", 0) == 0 or h2["expectancy_r"] <= 0:
        verdict = ("Holdout is positive at 1x costs but not at 2x: too fragile to trust. "
                   "Do not proceed.")
    else:
        verdict = ("Holdout expectancy is positive at 1x and 2x costs and the best cell was positive "
                   "on the selection period. Check the daily confidence intervals and the sample "
                   "size before reading anything into it; one holdout is not proof.")
    return "\n".join([
        "Candidate D — VWAP mean reversion, QQQ/SPY shares",
        f"cells tried: {res['cells_tried']}   (the best of N cells looks better than it is)",
        f"selection days: {res['selection_days']}   holdout days: {res['holdout_days']}",
        f"chosen on selection: z>={b.z_entry} stop {b.stop_pct}% target {b.target_pct}% "
        f"time {b.time_stop_min}m trend {b.trend_filter}",
        f"selection : n={s['n']} win {s['win_rate']:.1%} expectancy {s['expectancy_r']:+.3f}R",
        f"HOLDOUT   : n={h.get('n', 0)} win {h.get('win_rate', 0):.1%} "
        f"expectancy {h.get('expectancy_r', 0):+.3f}R  PF {h.get('profit_factor', 0):.2f}",
        f"HOLDOUT 2x costs: n={h2.get('n', 0)} expectancy {h2.get('expectancy_r', 0):+.3f}R",
        "holdout daily result, % of equity, by exposure cap (notional / equity):",
        *[
            f"  {cap:>4.2f}x: mean {d['mean_daily_pct']:+.3f} "
            f"[{d['mean_ci95'][0]:+.3f}, {d['mean_ci95'][1]:+.3f}]  "
            f"w/o best 5 days {d['mean_without_best5_pct']:+.3f}  "
            f">=0.3%: {d['days_ge_0.3']:.0%}  >=0.5%: {d['days_ge_0.5']:.0%}  "
            f"green {d['green_days']:.0%}  worst {d['worst_day_pct']:+.2f}%  "
            f"maxDD {d['max_drawdown_pct']:.2f}%"
            for cap, d in res["holdout_daily_by_cap"].items()
        ],
        verdict,
        "A holdout under ~200 trades is too small to confirm an edge.",
    ])


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("bars_csv")
    ap.add_argument("--symbols", nargs="*", default=["QQQ", "SPY"])
    ap.add_argument("--engine", choices=["python", "cpp"], default="python",
                    help="python is the reference; cpp is faster and must give identical trades")
    args = ap.parse_args(argv)
    df = pd.read_csv(args.bars_csv)
    df = df[df["symbol"].isin(args.symbols)]
    print(format_report(run_grid(prepare(load_bars(df)), engine=args.engine)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
