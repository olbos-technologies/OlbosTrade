#!/usr/bin/env python3
"""
Signal record report: how recorded signals turned out, per style, after costs.

Phase 1 of docs/SIGNAL_QUALITY_AUDIT.md. READ-ONLY: one SELECT, no writes, no
orders. Duplicate rows are collapsed in memory with the dedupe script's own
survivor rule, so the counts are real signals even before
scripts/dedupe_signal_outcomes.py --apply has been run.

Usage (on the server, inside the backend container):
    python scripts/signal_record_report.py
    python scripts/signal_record_report.py --cost-bps equity=10 --cost-bps crypto=30 --min-n 300
    python scripts/signal_record_report.py --json /tmp/signal_record.json

What it cannot report, and says so: options (spread signals are logged, never
resolved), day trading (the resolver walks daily bars), and per SETUP results
(signal_outcomes does not record which rule produced a signal).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

sys.path.insert(0, "/app")

from app.services.signal_record import (  # noqa: E402
    DEFAULT_COST_BPS, MIN_N, as_row, confidence_bucket, dedupe, meets_standard, summarize,
)

VIEWS = {
    "style": lambda r: (r["asset_type"],),
    "style x direction": lambda r: (r["asset_type"], r["action"]),
    "style x regime": lambda r: (r["asset_type"], r.get("regime") or "unknown"),
    "style x confidence": lambda r: (r["asset_type"], confidence_bucket(r.get("confidence"))),
    "style x engine version": lambda r: (r["asset_type"], r.get("signal_engine_version") or "unknown"),
}

NOT_COVERED = [
    "Options: spread signals are logged (options_signal_history) but never resolved, so they have no record yet.",
    "Day trading: the resolver walks DAILY bars, so intraday signals cannot be judged.",
    "Per setup: signal_outcomes does not store which rule fired, so results are by style, direction, regime and confidence only.",
]


def _pct(x):
    return "   -  " if x is None else f"{100 * x:5.1f}%"


def _r(x):
    return "   -  " if x is None else f"{x:+.3f}"


def _ci(ci, pct=False):
    if not ci:
        return "-"
    f = (lambda v: f"{100 * v:.1f}%") if pct else (lambda v: f"{v:+.3f}")
    return f"[{f(ci[0])}, {f(ci[1])}]"


def print_view(name, rows, gate_lb):
    print(f"\n── {name} " + "─" * max(0, 66 - len(name)))
    print(f"{'group':28} {'resolved':>8} {'days':>5} {'target':>7} {'win':>7}  {'win 95% CI':17} "
          f"{'avg win':>7} {'avg loss':>8} {'exp R':>7}  {'exp 95% CI':19} verdict")
    for s in rows:
        label = " / ".join(str(x) for x in s["group"])[:28]
        gate = "  PASSES GATE" if meets_standard(s, gate_lb) else ""
        print(f"{label:28} {s['resolved']:>8,} {s['days']:>5} {_pct(s['target_hit_rate']):>7} {_pct(s['win_rate']):>7}  "
              f"{_ci(s['win_rate_ci'], True):17} {_r(s['avg_win_r']):>7} {_r(s['avg_loss_r']):>8} "
              f"{_r(s['expectancy_r']):>7}  {_ci(s['expectancy_ci']):19} {s['verdict']}{gate}")


def parse_costs(items):
    out = {}
    for it in items or []:
        k, _, v = it.partition("=")
        out[k.strip()] = float(v)
    return out


async def load_rows():
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.signal_outcome import SignalOutcome as S

    async with AsyncSessionLocal() as s:
        res = await s.execute(select(
            S.ticker, S.asset_type, S.action, S.confidence, S.entry_price, S.stop_price, S.target_price,
            S.exit_price, S.status, S.regime, S.signal_engine_version, S.generated_at,
        ))
        return [as_row(dict(r._mapping)) for r in res]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Read-only signal record report (audit phase 1).")
    ap.add_argument("--cost-bps", action="append", metavar="STYLE=BPS",
                    help=f"round-trip cost allowance; defaults {DEFAULT_COST_BPS}")
    ap.add_argument("--min-n", type=int, default=MIN_N, help="minimum resolved signals for a verdict")
    ap.add_argument("--win-rate-lb", type=float, default=None,
                    help="optional gate: win-rate lower bound, e.g. 0.75")
    ap.add_argument("--json", metavar="PATH", help="also write the full result as JSON")
    args = ap.parse_args(argv)
    costs = {**DEFAULT_COST_BPS, **parse_costs(args.cost_bps)}

    raw = asyncio.run(load_rows())
    rows, dropped = dedupe(raw)
    print("=" * 70)
    print("SIGNAL RECORD — audit phase 1 (read-only)")
    print("=" * 70)
    print(f"rows read            : {len(raw):,}")
    print(f"duplicates collapsed : {dropped:,}  (same survivor rule as dedupe_signal_outcomes.py)")
    print(f"distinct signals     : {len(rows):,}")
    print(f"cost allowances (bps, round trip, ASSUMED): {costs}")
    print(f"minimum resolved for a verdict: {args.min_n}" +
          (f"; gate also needs win-rate lower bound >= {args.win_rate_lb:.0%}" if args.win_rate_lb else ""))
    print("R = multiple of the signal's own risk (entry to stop). Win = result above 0 after costs.")

    result = {"rows_read": len(raw), "duplicates_collapsed": dropped, "signals": len(rows),
              "cost_bps": costs, "min_n": args.min_n, "views": {}, "not_covered": NOT_COVERED}
    for name, key in VIEWS.items():
        summ = summarize(rows, key, costs, min_n=args.min_n)
        result["views"][name] = summ
        print_view(name, summ, args.win_rate_lb)

    print("\nNot covered by this report:")
    for line in NOT_COVERED:
        print(f"  - {line}")
    print("\nPast results do not predict future results.")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1, default=str)
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
