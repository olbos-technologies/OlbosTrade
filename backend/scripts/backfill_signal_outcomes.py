"""
One-off backfill for the signal-outcome label backlog.

Context: `check_pending_outcomes()` was opening a session and transaction per
row against a 120s scheduler budget, so it never finished. Only 32 of 102
tickers had ever received a label, and the resolved subset was a fragment of an
interrupted loop rather than a sample of the signals. The scheduler fix is in
381b6d4; this script drains the backlog that accumulated meanwhile.

Runs standalone inside the container — it does NOT require the fix to be
deployed. It deliberately imports `_resolve_one` and `_fetch_daily_bars` from
the app's own module rather than reimplementing them: the labelling decision
must come from the same reviewed code the scheduler uses. Only the batching and
iteration live here.

DRY RUN BY DEFAULT. Nothing is written without --apply.

Usage:
    python scripts/backfill_signal_outcomes.py            # measure only
    python scripts/backfill_signal_outcomes.py --apply    # write
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections import Counter

sys.path.insert(0, "/app")

from sqlalchemy import select, update  # noqa: E402

from app.core.database import AsyncSessionLocal  # noqa: E402
from app.models.signal_outcome import SignalOutcome  # noqa: E402
from app.services.signal_outcome_tracker import (  # noqa: E402
    DEFAULT_MAX_HOLD_DAYS,
    _fetch_daily_bars,
    _resolve_one,
    _resolved_payload,
)

WRITE_CHUNK = 1000


async def main(apply: bool) -> None:
    mode = "APPLY (writes)" if apply else "DRY RUN (no writes)"
    print(f"=== signal-outcome backfill — {mode} ===\n", flush=True)

    async with AsyncSessionLocal() as s:
        pending = (await s.execute(
            select(SignalOutcome).where(SignalOutcome.status == "pending")
        )).scalars().all()

    print(f"pending rows: {len(pending):,}", flush=True)
    if not pending:
        return

    by_ticker: dict[str, list] = {}
    for r in pending:
        by_ticker.setdefault(r.ticker, []).append(r)
    print(f"tickers:      {len(by_ticker)}\n", flush=True)

    outcomes = Counter()
    days_by_status: dict[str, list[int]] = {}
    payloads: list[dict] = []
    failed_tickers: list[str] = []
    started = time.monotonic()

    for i, (ticker, rows) in enumerate(sorted(by_ticker.items()), 1):
        try:
            earliest = min(r.generated_at for r in rows)
            hist = await _fetch_daily_bars(ticker, earliest)
        except Exception as exc:
            failed_tickers.append(ticker)
            print(f"  [{i}/{len(by_ticker)}] {ticker}: FETCH FAILED {exc}", flush=True)
            continue
        if hist is None or hist.empty:
            failed_tickers.append(ticker)
            print(f"  [{i}/{len(by_ticker)}] {ticker}: no bars", flush=True)
            continue

        local = Counter()
        for row in rows:
            res = _resolve_one(row, hist, DEFAULT_MAX_HOLD_DAYS)
            if res is None:
                local["unresolved"] += 1
                continue
            # Built by the tracker's own mapping rather than repeated here.
            # This script used to carry a parallel copy, which is exactly why
            # it drifted: _resolve_one grew from six return values to nine and
            # the copy kept unpacking six, so the backfill would have died with
            # ValueError on its first resolved row — and nothing caught it,
            # because scripts/ is not under test. Sharing the mapping means the
            # new excursion columns are persisted here too, for free.
            payload = _resolved_payload(row.id, res)
            status = payload["status"]
            local[status] += 1
            days_by_status.setdefault(status, []).append(payload["days_to_resolve"])
            payloads.append(payload)
        outcomes.update(local)
        print(f"  [{i}/{len(by_ticker)}] {ticker}: {dict(local)}", flush=True)

    elapsed = time.monotonic() - started

    # ── report ──
    print("\n" + "=" * 60)
    print("RESOLVED DISTRIBUTION (this backfill)")
    print("=" * 60)
    decisive = outcomes["target_hit"] + outcomes["stop_hit"]
    total = sum(outcomes.values())
    for k in ("target_hit", "stop_hit", "expired", "unresolved"):
        n = outcomes[k]
        print(f"  {k:12} {n:8,}  {100*n/total if total else 0:5.1f}%")
    if decisive:
        print(f"\n  target share of decided outcomes: "
              f"{100*outcomes['target_hit']/decisive:.1f}%")
        print("  (the pre-backfill figure was 6.7%, measured on a truncated "
              "and stop-biased subset)")
    for st, ds in sorted(days_by_status.items()):
        print(f"  avg bars to {st:11} {sum(ds)/len(ds):.2f}  (n={len(ds):,})")

    print(f"\n  tickers covered: {len(by_ticker)-len(failed_tickers)}/{len(by_ticker)}")
    if failed_tickers:
        print(f"  fetch failures:  {', '.join(failed_tickers)}")
    print(f"  elapsed:         {elapsed:.1f}s")
    print(f"  rows to write:   {len(payloads):,}")

    if not apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to persist.")
        return

    print(f"\nwriting {len(payloads):,} resolutions...", flush=True)
    written = 0
    async with AsyncSessionLocal() as s:
        async with s.begin():
            for i in range(0, len(payloads), WRITE_CHUNK):
                chunk = payloads[i:i + WRITE_CHUNK]
                await s.execute(update(SignalOutcome), chunk)
                written += len(chunk)
                print(f"  {written:,}/{len(payloads):,}", flush=True)
    print(f"done — {written:,} rows updated.")


if __name__ == "__main__":
    asyncio.run(main(apply="--apply" in sys.argv))
