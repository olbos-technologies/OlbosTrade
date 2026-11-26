"""Fixed-percentage exit geometries on IDENTICAL entries: does the exit rule, or the entry, carry the result?

Read-only research. No broker, no orders, no database. Reads the same 5-minute regular-hours CSV as
vwap_reversion_backtest.py (made by intraday_bars_fetch.py) and reuses its trade simulator, so every
conservative convention there applies here too: entry at the NEXT bar's open plus slippage, the stop is
assumed to fill first when one bar touches both, a bar that opens beyond the stop fills at the OPEN, a
target must trade THROUGH by one tick, slippage on stops and time exits, commission on both sides, and
every position is flat by 15:55 ET (an intraday strategy: nothing is held overnight).

    python scripts/fixed_exit_geometry_study.py bars.csv [--symbols QQQ SPY] [--notional 10000]

WHAT IT TESTS
  Geometries (take profit / stop loss, percent of the entry price):
    A  0.50 / 1.00   the proposal under test
    B  0.50 / 0.50
    C  0.50 / 0.25
    D  1.00 / 0.50
    E  adaptive: target 1.0 x and stop 1.5 x the symbol's recent hourly-equivalent ATR
  Entry sets (the SAME entries are used for every geometry, so only the exit differs):
    grid  a long entry every 30 minutes from 10:00 to 14:30 ET on every day. This is the NULL entry:
          it has no signal in it. If a geometry is "profitable" here it is the geometry and the drift
          of the market, not skill.
    orb   one long entry a day: the first 5-minute close above the 09:30-10:00 high after 10:00.
          A common textbook rule, included as ONE example of a real entry, not as a recommendation.

HOW TO READ IT
  * "break-even win rate" is the win rate a geometry needs to cover its own costs. 0.5 take profit with a
    1.0 stop needs two wins in three BEFORE costs and more after them.
  * A geometry that wins often but loses more when it loses can have a high win rate and a negative mean.
    The mean net result per trade, after costs, with its bootstrap range, is the number to look at.
  * "first half / second half" is the mean over the earlier and later half of the days. A result that
    changes sign between them is not stable.
  * Nothing here is a forecast, a verdict, or approval to trade. It is evidence about the past under these
    assumptions. Whether a rule works is for the Edge Lab ledger to decide, with its multiple-testing bar.
"""
from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vwap_reversion_backtest as v  # noqa: E402  (reuses the simulator and the data loader)

GRID_TIMES = tuple(range(10 * 60, 14 * 60 + 31, 30))      # 10:00 ... 14:30 ET, minutes since midnight
ORB_START, ORB_END = 9 * 60 + 30, 10 * 60
ATR_LOOKBACK = 14
HOURLY_FROM_5M = math.sqrt(12)                            # 12 five-minute bars in an hour


@dataclass(frozen=True)
class Geometry:
    name: str
    label: str
    take_pct: Optional[float] = None      # percent of entry; None for the adaptive geometry
    stop_pct: Optional[float] = None
    atr_take: Optional[float] = None      # multiples of hourly-equivalent ATR (adaptive only)
    atr_stop: Optional[float] = None

    @property
    def adaptive(self) -> bool:
        return self.atr_take is not None


GEOMETRIES = (
    Geometry("A", "0.5% / 1.0%", take_pct=0.5, stop_pct=1.0),
    Geometry("B", "0.5% / 0.5%", take_pct=0.5, stop_pct=0.5),
    Geometry("C", "0.5% / 0.25%", take_pct=0.5, stop_pct=0.25),
    Geometry("D", "1.0% / 0.5%", take_pct=1.0, stop_pct=0.5),
    Geometry("E", "1.0x / 1.5x ATR", atr_take=1.0, atr_stop=1.5),
)


# ── pure arithmetic ─────────────────────────────────────────────────────────

def breakeven_win_rate(take_pct: float, stop_pct: float, cost_pct: float = 0.0) -> float:
    """The win rate at which the mean net result is zero.

    A win nets take - cost, a loss nets -(stop + cost), so w*(take - cost) = (1-w)*(stop + cost),
    giving w = (stop + cost) / (take + stop). Costs are one round trip, as a percent of the entry price.
    """
    if take_pct <= 0 or stop_pct <= 0 or cost_pct < 0:
        raise ValueError("take, stop must be above zero and cost must not be negative")
    return (stop_pct + cost_pct) / (take_pct + stop_pct)


def driftless_win_rate(take_pct: float, stop_pct: float) -> float:
    """P(price reaches +take before -stop) for a driftless random walk: stop / (take + stop).

    With no edge in the entry this is the best a fixed geometry can do, and it is exactly its
    break-even rate before costs: the geometry alone has zero expectancy, and costs make it negative.
    """
    return breakeven_win_rate(take_pct, stop_pct, 0.0)


def hourly_atr_pct(h: np.ndarray, l: np.ndarray, c: np.ndarray, i: int, lookback: int = ATR_LOOKBACK) -> Optional[float]:
    """Average true range of the `lookback` 5-minute bars ending at bar i, scaled to an hour, as a percent
    of that bar's close. None until there are enough bars: a number is never invented from too few."""
    if i < lookback:
        return None
    lo, hi = i - lookback + 1, i + 1
    prev_close = c[lo - 1:hi - 1]
    tr = np.maximum.reduce([h[lo:hi] - l[lo:hi], np.abs(h[lo:hi] - prev_close), np.abs(l[lo:hi] - prev_close)])
    atr5 = float(np.mean(tr))
    if not np.isfinite(atr5) or atr5 <= 0 or c[i] <= 0:
        return None
    return atr5 * HOURLY_FROM_5M / c[i] * 100.0


def bootstrap_mean_ci(x: np.ndarray, seed: int = 7, boots: int = 2000) -> tuple[float, float]:
    if len(x) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = rng.choice(x, size=(boots, len(x)), replace=True).mean(axis=1)
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


# ── entries (identical for every geometry) ──────────────────────────────────

def grid_entries(end_min: np.ndarray) -> list[int]:
    """Signal-bar indices: the bar that ENDS at each grid time. The entry is the next bar's open."""
    wanted = set(GRID_TIMES)
    return [i for i in range(len(end_min) - 1) if int(end_min[i]) in wanted]


def orb_entries(h: np.ndarray, c: np.ndarray, end_min: np.ndarray) -> list[int]:
    """At most one signal bar a day: the first close above the 09:30-10:00 high, after 10:00."""
    in_range = [i for i in range(len(end_min)) if ORB_START < int(end_min[i]) <= ORB_END]
    if len(in_range) < 3:
        return []                                                 # the opening range is incomplete: no entry
    or_high = float(np.max(h[in_range]))
    for i in range(len(end_min) - 1):
        if int(end_min[i]) > ORB_END and c[i] > or_high and int(end_min[i + 1]) < v.FLATTEN_MIN:
            return [i]
    return []


# ── simulation ──────────────────────────────────────────────────────────────

def run(bars: pd.DataFrame, geometry: Geometry, entry_set: str, base: Optional[v.Params] = None) -> pd.DataFrame:
    """One row per trade: symbol, date, entry, exit, reason, net_pct (after costs)."""
    base = base or v.Params()
    rows = []
    for (sym, _d), day in bars.groupby(["symbol", "date"], sort=False):
        day = day.reset_index(drop=True)
        o, h, l, c = (day[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        em, et = day["end_min"].to_numpy(), list(day["end"])
        signals = grid_entries(em) if entry_set == "grid" else orb_entries(h, c, em)
        for i in signals:
            j = i + 1
            if em[j] >= v.FLATTEN_MIN:
                continue
            if geometry.adaptive:
                a = hourly_atr_pct(h, l, c, i)
                if a is None:
                    continue                                        # no ATR yet: skip, never guess
                take, stop = geometry.atr_take * a, geometry.atr_stop * a
            else:
                take, stop = geometry.take_pct, geometry.stop_pct
            p = replace(base, target_pct=take, stop_pct=stop, be_trigger_pct=1e9, time_stop_min=10_000)
            k, exit_px, reason, entry = v._run_trade(o, h, l, c, em, et, j, 1, p)
            comm = p.commission * p.cost_mult
            net_pct = ((exit_px - entry) - 2 * comm) / entry * 100.0
            rows.append((sym, day["date"].iloc[0], et[j] - pd.Timedelta(minutes=v.BAR_MIN), et[k], reason, net_pct, take, stop))
    return pd.DataFrame(rows, columns=["symbol", "date", "entry_ts", "exit_ts", "reason", "net_pct", "take_pct", "stop_pct"])


def summarize(t: pd.DataFrame, notional: float, cost_pct_est: float) -> dict:
    if t.empty:
        return {"n": 0}
    x = t["net_pct"].to_numpy(float)
    wins = float((x > 0).mean())
    lo, hi = bootstrap_mean_ci(x)
    dates = sorted(t["date"].unique())
    half = dates[len(dates) // 2] if dates else None
    first = t[t["date"] < half]["net_pct"] if half is not None else t["net_pct"]
    second = t[t["date"] >= half]["net_pct"] if half is not None else t["net_pct"]
    take, stop = float(t["take_pct"].median()), float(t["stop_pct"].median())
    reasons = t["reason"].value_counts(normalize=True).to_dict()
    return {
        "n": int(len(t)), "win_rate": wins, "mean_pct": float(x.mean()), "ci": (lo, hi),
        "mean_dollars": float(x.mean()) / 100.0 * notional,
        "breakeven_win": breakeven_win_rate(take, stop, cost_pct_est),
        "driftless_win": driftless_win_rate(take, stop),
        "first_half": float(first.mean()) if len(first) else float("nan"),
        "second_half": float(second.mean()) if len(second) else float("nan"),
        "reasons": reasons, "take": take, "stop": stop,
    }


def format_report(results: dict, notional: float, symbols: list[str], n_days: int, price_hint: float) -> str:
    cost_pct = 2 * (v.Params().slippage + v.Params().commission) / price_hint * 100.0
    out = ["=" * 78, "FIXED EXIT GEOMETRIES ON IDENTICAL ENTRIES  (read-only research, not a forecast)", "=" * 78,
           f"symbols: {', '.join(symbols)}   days: {n_days}   notional per trade: ${notional:,.0f}",
           f"assumed round-trip cost ~ {cost_pct:.3f}% of price (slippage + commission at a ${price_hint:.0f} share)",
           "Long-only, flat by 15:55 ET, entry at the next bar's open. Every entry is simulated on its own (overlap between trades is",
           "ignored): these are per-trade statistics, not a portfolio. Every geometry sees exactly the same entries.", ""]
    for entry_set, label in (("grid", "NULL entries (a long every 30 min, no signal)"), ("orb", "OPENING-RANGE-BREAKOUT entries (one example of a real rule)")):
        out += ["-" * 78, label, "-" * 78,
                f"{'geom':<18}{'n':>6}{'win%':>7}{'b/e%':>7}{'mean %':>9}{'95% range':>20}{'$/trade':>9}{'1st half':>9}{'2nd half':>9}"]
        for g in GEOMETRIES:
            s = results[(entry_set, g.name)]
            if s["n"] == 0:
                out.append(f"{g.name + ' ' + g.label:<18}{'0':>6}   no trades")
                continue
            if s["n"] < 30:
                out.append(f"{g.name + ' ' + g.label:<18}{s['n']:>6}   INSUFFICIENT DATA (under 30 trades: no mean is shown)")
                continue
            be = breakeven_win_rate(s["take"], s["stop"], cost_pct) * 100
            out.append(f"{g.name + ' ' + g.label:<18}{s['n']:>6}{s['win_rate'] * 100:>7.1f}{be:>7.1f}{s['mean_pct']:>9.3f}"
                       f"{'[%+.3f, %+.3f]' % s['ci']:>20}{s['mean_dollars']:>9.2f}{s['first_half']:>9.3f}{s['second_half']:>9.3f}")
            rs = ", ".join(f"{k} {v_ * 100:.0f}%" for k, v_ in sorted(s["reasons"].items(), key=lambda kv: -kv[1]))
            out.append(f"{'':<18}exits: {rs}")
        out.append("")
    out += ["win% = share of trades with a positive net result; b/e% = win rate needed to cover costs for that geometry.",
            "A result is only interesting if its 95% range excludes zero AND both halves agree in sign. Even then it is one",
            "backtest of one period: it is a hypothesis for the Edge Lab ledger, not evidence of an edge to trade.",
            "On the NULL entries a geometry has no signal to help it: a positive mean there is the market's drift, not skill."]
    return "\n".join(out)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("bars", help="5-minute bars CSV from intraday_bars_fetch.py")
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--notional", type=float, default=10_000.0)
    a = ap.parse_args(argv)
    bars = v.load_bars(pd.read_csv(a.bars))
    if a.symbols:
        bars = bars[bars["symbol"].isin([s.upper() for s in a.symbols])]
    if bars.empty:
        print("no bars to read", file=sys.stderr)
        return 2
    base = v.Params()
    price_hint = float(bars["close"].median())
    cost_pct = 2 * (base.slippage + base.commission) / price_hint * 100.0
    results = {}
    for entry_set in ("grid", "orb"):
        for g in GEOMETRIES:
            results[(entry_set, g.name)] = summarize(run(bars, g, entry_set, base), a.notional, cost_pct)
    print(format_report(results, a.notional, sorted(bars["symbol"].unique()), int(bars["date"].nunique()), price_hint))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
