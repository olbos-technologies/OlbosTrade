"""
Do higher options signal scores come with better closed-trade outcomes?

Context: Aggressive requires an options signal_score of at least 0.70 at the
frequency controller, while the scan approves on the scorer's own, lower
threshold. Before moving either number, this answers a narrower question from
the data: of the options trades that actually closed, how did each score band
do? In particular, did the 0.62-0.70 band (the one a lower floor would admit)
do worse than 0.70 and above?

What it reads (read-only, no writes, no orders):
  * `trades`                  closed options trades with a score and a known P&L
  * `options_signal_history`  how many signals each band produced

What it will NOT do: promise that a threshold improves returns. Closed options
trades are few; with fewer than MIN_N in a band the script says INSUFFICIENT DATA
for that comparison instead of reading anything into noise. The score is the AI
scorer's signal_score; whether it carries any skill is exactly what a band
table with enough trades would show, and without enough trades it shows nothing.

Usage (on the server, inside the backend container):
    python scripts/options_score_band_report.py [--days 90]
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

sys.path.insert(0, "/app")

# Band edges on signal_score. 0.62 is Aggressive's own signal threshold and 0.70
# is the frequency controller's floor, so the 0.62-0.70 band is what lowering the
# controller to 0.62 would newly admit.
EDGES: tuple[float, ...] = (0.0, 0.55, 0.62, 0.70, 1.0001)

#: Fewest closed trades in each of two bands before a comparison means anything.
MIN_N = 30

#: Options strategies (kept in step with app.services.options_funnel.KNOWN_STRATEGIES;
#: duplicated so this script and its tests import nothing that needs a database).
OPTIONS_STRATEGIES = ("bull_put_spread", "bear_call_spread", "iron_condor",
                      "bull_call_debit_spread", "bear_put_spread")


def band_label(lo: float, hi: float) -> str:
    return f"< {hi:.2f}" if lo <= 0 else (f">= {lo:.2f}" if hi > 1 else f"{lo:.2f} - {hi:.2f}")


def band_of(score: float, edges: Sequence[float] = EDGES) -> Optional[int]:
    """Index of the band a score falls in, or None when it is outside every band."""
    for i in range(len(edges) - 1):
        if edges[i] <= score < edges[i + 1]:
            return i
    return None


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval for a win rate. (nan, nan) when n is 0."""
    if n == 0:
        return (float("nan"), float("nan"))
    p, d = k / n, 1 + z ** 2 / n
    c = (p + z ** 2 / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2)) / d
    return (c - h, c + h)


def summarize_bands(rows: Sequence[tuple[float, float]], edges: Sequence[float] = EDGES) -> list[dict]:
    """rows are (signal_score, pnl) for closed trades with both known. One dict per band."""
    out = []
    for i in range(len(edges) - 1):
        pnls = [p for s, p in rows if band_of(s, edges) == i]
        n = len(pnls)
        wins = sum(1 for p in pnls if p > 0)
        lo, hi = wilson(wins, n)
        out.append({
            "band": band_label(edges[i], edges[i + 1]),
            "n": n,
            "wins": wins,
            "win_rate": (wins / n) if n else None,
            "ci_low": None if n == 0 else lo,
            "ci_high": None if n == 0 else hi,
            "avg_pnl": (sum(pnls) / n) if n else None,
            "total_pnl": sum(pnls) if n else 0.0,
        })
    return out


def comparison_verdict(bands: list[dict], min_n: int = MIN_N) -> str:
    """One honest sentence on 0.62-0.70 versus 0.70+, or why there is no comparison."""
    mid, top = bands[2], bands[3]
    if mid["n"] < min_n or top["n"] < min_n:
        return (f"INSUFFICIENT DATA: {mid['band']} has {mid['n']} closed trade(s) and {top['band']} has {top['n']}; "
                f"at least {min_n} in each is needed before the two can be compared. Nothing here supports raising "
                "or lowering a threshold.")
    # Both bands have enough trades: say whether their win-rate intervals separate.
    if mid["ci_high"] < top["ci_low"]:
        return (f"{mid['band']} won {mid['win_rate']:.0%} against {top['win_rate']:.0%} for {top['band']}, and the 95% "
                "intervals do not overlap: the lower band did worse on this sample.")
    if top["ci_high"] < mid["ci_low"]:
        return (f"{mid['band']} won {mid['win_rate']:.0%} against {top['win_rate']:.0%} for {top['band']}, and the 95% "
                "intervals do not overlap: the higher band did worse on this sample.")
    return (f"{mid['band']} won {mid['win_rate']:.0%} against {top['win_rate']:.0%} for {top['band']}; the 95% "
            "intervals overlap, so this sample shows no reliable difference between them.")


def _pct(x: Optional[float]) -> str:
    return "  -  " if x is None else f"{x:5.0%}"


def _money(x: Optional[float]) -> str:
    return "      -" if x is None else f"{x:>+8,.0f}"


async def main(days: int) -> None:
    # DB imports live here so the pure functions above can be tested with no database.
    from sqlalchemy import func, select

    from app.core.database import AsyncSessionLocal
    from app.models.options_signal_history import OptionsSignalHistory
    from app.models.trade import Trade

    since = datetime.now(timezone.utc) - timedelta(days=days)
    async with AsyncSessionLocal() as session:
        closed = (await session.execute(
            select(Trade.signal_score, Trade.pnl).where(
                Trade.strategy.in_(OPTIONS_STRATEGIES),
                Trade.status.in_(("closed", "expired")),
                Trade.exit_date >= since,
                Trade.signal_score.is_not(None),
                Trade.pnl.is_not(None),
            )
        )).all()
        unscored = int((await session.execute(
            select(func.count()).select_from(Trade).where(
                Trade.strategy.in_(OPTIONS_STRATEGIES),
                Trade.status.in_(("closed", "expired")),
                Trade.exit_date >= since,
                (Trade.signal_score.is_(None)) | (Trade.pnl.is_(None)),
            )
        )).scalar() or 0)
        still_open = int((await session.execute(
            select(func.count()).select_from(Trade).where(
                Trade.strategy.in_(OPTIONS_STRATEGIES), Trade.status == "open",
            )
        )).scalar() or 0)
        sig_scores = [float(s) for (s,) in (await session.execute(
            select(OptionsSignalHistory.signal_score).where(OptionsSignalHistory.generated_at >= since)
        )).all()]

    rows = [(float(s), float(p)) for s, p in closed]
    bands = summarize_bands(rows)

    print("=" * 76)
    print(f"OPTIONS SCORE BANDS - closed trades exited in the last {days} day(s)")
    print("=" * 76)
    print(f"closed options trades with a score and P&L : {len(rows):,}")
    print(f"closed but missing a score or a P&L        : {unscored:,}  (left out, not counted as zero)")
    print(f"options trades still open                  : {still_open:,}  (no outcome yet)")

    print("\n" + "-" * 76)
    print(f"{'score band':<14}{'closed':>7}{'wins':>6}{'win rate':>10}   {'95% interval':<14}{'avg P&L':>9}{'total P&L':>11}")
    print("-" * 76)
    for b in bands:
        ci = "      -" if b["n"] == 0 else f"{b['ci_low']:.0%} - {b['ci_high']:.0%}"
        print(f"{b['band']:<14}{b['n']:>7}{b['wins']:>6}{_pct(b['win_rate']):>10}   {ci:<14}{_money(b['avg_pnl']):>9}{_money(b['total_pnl'] if b['n'] else None):>11}")

    print("\n" + "-" * 76)
    print("SIGNALS GENERATED PER BAND (how many ideas each band produces)")
    print("-" * 76)
    counts = [0] * (len(EDGES) - 1)
    for s in sig_scores:
        i = band_of(s)
        if i is not None:
            counts[i] += 1
    for i, c in enumerate(counts):
        print(f"  {band_label(EDGES[i], EDGES[i + 1]):<14}{c:>8,}")

    print("\n" + "-" * 76)
    print("READ THIS")
    print("-" * 76)
    print(comparison_verdict(bands))
    print("\nThis is paper trading with a short history. A win rate is not a return, and a")
    print("band table cannot show that a threshold improves results; it can only show")
    print("whether the data so far argues against one. Open trades are not counted.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=90)
    args = parser.parse_args()
    asyncio.run(main(args.days))
