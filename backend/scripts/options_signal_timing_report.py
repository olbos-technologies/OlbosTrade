"""
When do the high-scoring options signals appear, and what happens to them?

Context: Aggressive needs an options signal_score of at least 0.70 at execution,
yet no options trade has been placed in 90 days even though hundreds of signals
scored 0.70 or more. One candidate explanation is timing: the scan runs around the
clock, execution only works in regular trading hours (09:30-16:00 ET, Mon-Fri),
and an attempt made while the market is closed is refused by design. This checks
that against the data instead of assuming it.

What it reads (read-only, no writes, no orders):
  * `options_signal_history`  every signal that qualified at the scan
  * `execution_events`        every options execution attempt and its outcome

What it reports:
  1. For each score band, how many signals were generated inside versus outside
     regular trading hours, so the 0.70+ band can be compared with everything else.
  2. For 0.70+ signals, the Eastern-time hour they were generated in.
  3. For 0.70+ signals, the first execution outcome recorded for the same ticker
     within MATCH_MINUTES of generation (or that none was recorded).

What it will NOT do: say why an attempt was refused beyond the reason the system
itself recorded, or promise that any change would produce trades. A signal and an
attempt are matched by ticker and time, which is an approximation, and says so.

Usage (on the server, inside the backend container):
    python scripts/options_signal_timing_report.py [--days 90]
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Sequence

sys.path.insert(0, "/app")

#: Score bands on signal_score. 0.70 is the frequency controller's Aggressive floor.
EDGES: tuple[float, ...] = (0.0, 0.62, 0.70, 1.0001)

#: A signal and an execution attempt are matched when they share a ticker and the
#: attempt is recorded within this many minutes after the signal was generated
#: (the scan re-runs every 30 minutes, so a later attempt belongs to a later scan).
MATCH_MINUTES = 30

#: Fewest signals in a band before an in-hours share is compared across bands.
MIN_N = 30


def band_label(lo: float, hi: float) -> str:
    return f"< {hi:.2f}" if lo <= 0 else (f">= {lo:.2f}" if hi > 1 else f"{lo:.2f} - {hi:.2f}")


def band_of(score: float, edges: Sequence[float] = EDGES) -> Optional[int]:
    for i in range(len(edges) - 1):
        if edges[i] <= score < edges[i + 1]:
            return i
    return None


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval for a share. (nan, nan) when n is 0."""
    if n == 0:
        return (float("nan"), float("nan"))
    p, d = k / n, 1 + z ** 2 / n
    c = (p + z ** 2 / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2)) / d
    return (c - h, c + h)


def _default_is_open() -> Callable[[datetime], bool]:
    from app.utils.market_hours import is_market_open
    return is_market_open


def summarize_timing(
    signals: Sequence[tuple[float, datetime]],
    is_open: Optional[Callable[[datetime], bool]] = None,
    edges: Sequence[float] = EDGES,
) -> dict:
    """signals are (signal_score, generated_at). Per band: total, inside and outside regular hours,
    and, for every band, the Eastern-time hour histogram."""
    is_open = is_open or _default_is_open()
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    n_bands = len(edges) - 1
    total = [0] * n_bands
    inside = [0] * n_bands
    hours = [Counter() for _ in range(n_bands)]
    for score, at in signals:
        i = band_of(score, edges)
        if i is None:
            continue
        total[i] += 1
        if is_open(at):
            inside[i] += 1
        hours[i][at.astimezone(et).hour] += 1
    bands = []
    for i in range(n_bands):
        lo, hi = wilson(inside[i], total[i])
        bands.append({
            "band": band_label(edges[i], edges[i + 1]),
            "signals": total[i],
            "in_hours": inside[i],
            "out_of_hours": total[i] - inside[i],
            "share_in_hours": (inside[i] / total[i]) if total[i] else None,
            "ci_low": None if total[i] == 0 else lo,
            "ci_high": None if total[i] == 0 else hi,
            "hours_et": dict(sorted(hours[i].items())),
        })
    return {"bands": bands}


def timing_verdict(summary: dict, min_n: int = MIN_N) -> str:
    """One honest sentence comparing the top band's in-hours share with all other signals."""
    top = summary["bands"][-1]
    rest_total = sum(b["signals"] for b in summary["bands"][:-1])
    rest_in = sum(b["in_hours"] for b in summary["bands"][:-1])
    if top["signals"] < min_n or rest_total < min_n:
        return (f"INSUFFICIENT DATA: {top['band']} has {top['signals']} signal(s) and the other bands {rest_total}; "
                f"at least {min_n} in each is needed before their timing can be compared.")
    rlo, rhi = wilson(rest_in, rest_total)
    rshare = rest_in / rest_total
    head = (f"{top['in_hours']} of {top['signals']} {top['band']} signals ({top['share_in_hours']:.0%}) were generated "
            f"in regular trading hours, against {rshare:.0%} of the other signals. ")
    if top["ci_high"] < rlo:
        return head + ("The 95% intervals do not overlap: the high-scoring signals are generated in market hours "
                       "LESS often than the rest, which fits the closed-market explanation.")
    if top["ci_low"] > rhi:
        return head + ("The 95% intervals do not overlap: the high-scoring signals are generated in market hours "
                       "MORE often than the rest, which argues against a closed-market explanation.")
    return head + ("The 95% intervals overlap: this sample shows no reliable timing difference, so timing alone "
                   "does not explain it.")


def match_attempts(
    signals: Sequence[tuple[str, datetime]],
    events: Sequence[tuple[str, datetime, Optional[str], Optional[str]]],
    window_minutes: int = MATCH_MINUTES,
) -> Counter:
    """signals are (ticker, generated_at); events are (ticker, created_at, status, reason).

    Each signal is matched to the earliest execution event for the same ticker recorded in
    [generated_at, generated_at + window]. The label is the recorded status plus, for a
    refusal, the funnel's stable bucket for the reason. No event -> 'no execution attempt recorded'."""
    from app.services.options_funnel import bucket_execution_reason

    by_ticker: dict[str, list[tuple[datetime, Optional[str], Optional[str]]]] = {}
    for ticker, at, status, reason in events:
        by_ticker.setdefault(ticker, []).append((at, status, reason))
    for evs in by_ticker.values():
        evs.sort(key=lambda e: e[0])
    window = timedelta(minutes=window_minutes)
    out: Counter = Counter()
    for ticker, gen in signals:
        hit = next((e for e in by_ticker.get(ticker, []) if gen <= e[0] <= gen + window), None)
        if hit is None:
            out["no execution attempt recorded"] += 1
            continue
        _, status, reason = hit
        status = status or "unknown"
        if status in ("blocked", "skipped"):
            out[f"{status}: {bucket_execution_reason(reason)}"] += 1
        else:
            out[status] += 1
    return out


def _pct(x: Optional[float]) -> str:
    return "   -" if x is None else f"{x:4.0%}"


async def main(days: int) -> None:
    # DB imports live here so the pure functions above can be tested with no database.
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.execution_event import ExecutionEvent
    from app.models.options_signal_history import OptionsSignalHistory

    since = datetime.now(timezone.utc) - timedelta(days=days)
    async with AsyncSessionLocal() as session:
        sig_rows = (await session.execute(
            select(OptionsSignalHistory.ticker, OptionsSignalHistory.signal_score, OptionsSignalHistory.generated_at)
            .where(OptionsSignalHistory.generated_at >= since)
        )).all()
        ev_rows = (await session.execute(
            select(ExecutionEvent.ticker, ExecutionEvent.created_at, ExecutionEvent.status,
                   ExecutionEvent.payload["reason"].as_string())
            .where(ExecutionEvent.kind == "execution", ExecutionEvent.asset_type == "options",
                   ExecutionEvent.created_at >= since)
        )).all()

    scored = [(float(s), g) for _, s, g in sig_rows]
    summary = summarize_timing(scored)

    print("=" * 76)
    print(f"OPTIONS SIGNAL TIMING - signals generated in the last {days} day(s)")
    print("=" * 76)
    print(f"signals qualified at the scan : {len(scored):,}")
    print(f"options execution attempts    : {len(ev_rows):,}")

    print("\n" + "-" * 76)
    print("WHEN EACH BAND WAS GENERATED (regular hours = 09:30-16:00 ET, Mon-Fri, no holidays)")
    print("-" * 76)
    print(f"{'score band':<14}{'signals':>9}{'in hours':>10}{'outside':>9}{'share in hours':>16}   95% interval")
    for b in summary["bands"]:
        ci = "-" if b["signals"] == 0 else f"{b['ci_low']:.0%} - {b['ci_high']:.0%}"
        print(f"{b['band']:<14}{b['signals']:>9,}{b['in_hours']:>10,}{b['out_of_hours']:>9,}{_pct(b['share_in_hours']):>16}   {ci}")

    top = summary["bands"][-1]
    print("\n" + "-" * 76)
    print(f"ET HOUR THE {top['band']} SIGNALS WERE GENERATED IN")
    print("-" * 76)
    if not top["hours_et"]:
        print("  (none)")
    for h, n in top["hours_et"].items():
        print(f"  {h:02d}:00  {'#' * min(n, 60)} {n}")

    top_signals = [(t, g) for t, s, g in sig_rows if band_of(float(s)) == len(EDGES) - 2]
    ev = [(t, c, s, r) for t, c, s, r in ev_rows]
    outcomes = match_attempts(top_signals, ev)
    print("\n" + "-" * 76)
    print(f"WHAT HAPPENED TO THE {top['band']} SIGNALS (first attempt for the same ticker within {MATCH_MINUTES} min)")
    print("-" * 76)
    if not top_signals:
        print("  (no signals in this band)")
    for label, n in outcomes.most_common():
        print(f"  {n:6,}  {label}")

    print("\n" + "-" * 76)
    print("READ THIS")
    print("-" * 76)
    print(timing_verdict(summary))
    print("\nA signal is matched to an attempt by ticker and time, which is an approximation.")
    print("This shows what the system recorded, not why a threshold or a limit should change.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=90)
    args = parser.parse_args()
    asyncio.run(main(args.days))
