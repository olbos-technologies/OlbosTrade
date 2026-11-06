"""
Why has the options scanner not fired?

Options spread scanning has covered the full ~102-symbol watchlist every 30
minutes since 2026-08-01, and produced zero closed options trades in the four
weeks to the 2026-08-28 assessment. This reads options_scan_rejections and
reports where candidates actually die, so that question is answered from data
rather than from reading the gate code and guessing.

Read-only. No writes, no orders.

What to look for, and what each answer implies:

  * One reason dominating -> that gate is the binding constraint. Fix or
    reconsider that one thing; everything else is noise.
  * "insufficient price history" high -> a data problem, not a strategy
    problem.
  * Entry-condition reasons spread across strategies -> the strategies are
    genuinely not finding setups; widening the universe again will not help.
  * Rejections concentrated in one regime -> the regime->strategy map is the
    constraint. LOW_VOL_TRENDING allows only bull_call_debit_spread, so a long
    stretch in that regime with few debit setups produces exactly this.
  * Few rejections AND no trades is inconclusive: old builds did not record
    regime and risk-state early returns. Verify deployed build and scheduler
    activity before inferring a downstream block or daily-cap contention.

Usage:
    python scripts/options_rejection_report.py [--days 7]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")

from sqlalchemy import select  # noqa: E402

from app.core.database import AsyncSessionLocal  # noqa: E402
from app.models.options_scan_rejection import OptionsScanRejection  # noqa: E402


def _bar(n: int, total: int, width: int = 28) -> str:
    filled = 0 if total == 0 else round(width * n / total)
    return "#" * filled + "." * (width - filled)


async def main(days: int) -> None:
    since = datetime.now(timezone.utc) - timedelta(days=days)

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(OptionsScanRejection).where(
                OptionsScanRejection.first_seen_at >= since
            )
        )).scalars().all()

    print("=" * 72)
    print(f"OPTIONS SCAN REJECTIONS — last {days} day(s)")
    print("=" * 72)

    if not rows:
        print("\nNo rejection rows in this window.")
        print("\nInconclusive: check the deployed version, scheduler and regime gate.")
        print("Older builds omitted some early exits. No rows does not establish that any candidate qualified.")
        return

    total_events = sum(r.occurrences for r in rows)
    distinct_days = len({r.first_seen_at.date() for r in rows})
    print(f"distinct (ticker, strategy, reason, day) rows : {len(rows):,}")
    print(f"total scan events behind them                 : {total_events:,}")
    print(f"trading days covered                          : {distinct_days}")
    print(f"tickers touched                               : {len({r.ticker for r in rows})}")

    by_reason = Counter()
    for r in rows:
        by_reason[r.reason] += r.occurrences

    print("\n" + "-" * 72)
    print("WHERE CANDIDATES DIE (by scan events)")
    print("-" * 72)
    for reason, n in by_reason.most_common(15):
        pct = 100 * n / total_events
        print(f"  {_bar(n, total_events)}  {pct:5.1f}%  {n:6,}  {reason[:40]}")

    by_strategy = Counter()
    for r in rows:
        by_strategy[r.strategy or "(none selected)"] += r.occurrences
    print("\n" + "-" * 72)
    print("BY STRATEGY")
    print("-" * 72)
    for strategy, n in by_strategy.most_common():
        print(f"  {strategy:32} {n:7,}  ({100*n/total_events:5.1f}%)")

    by_regime = Counter()
    for r in rows:
        by_regime[r.regime or "(unknown)"] += r.occurrences
    print("\n" + "-" * 72)
    print("BY REGIME  — which strategies were even allowed at the time")
    print("-" * 72)
    for regime, n in by_regime.most_common():
        print(f"  {regime:32} {n:7,}  ({100*n/total_events:5.1f}%)")

    # A reason that hits nearly every ticker is a systemic gate; one that hits
    # a handful is a per-symbol data or liquidity issue. The distinction
    # decides whether the fix is in the gate or in the universe.
    spread: dict[str, set] = defaultdict(set)
    for r in rows:
        spread[r.reason].add(r.ticker)
    print("\n" + "-" * 72)
    print("REACH — distinct tickers hitting each reason (systemic vs per-symbol)")
    print("-" * 72)
    for reason, n in by_reason.most_common(10):
        print(f"  {len(spread[reason]):4} tickers   {reason[:52]}")

    print("\n" + "-" * 72)
    print("CAVEAT")
    print("-" * 72)
    print("This reports scan-stage rejections, not a complete execution funnel.")
    print("New builds include regime/risk-state early returns and worker timeouts.")
    print("A short list alone does not identify the blocker. Check execution mode,")
    print("execution logs, capacity, broker responses, and deployed version separately.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    asyncio.run(main(args.days))
