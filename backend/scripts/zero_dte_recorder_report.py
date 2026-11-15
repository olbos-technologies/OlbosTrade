"""What the 0DTE recorder has seen: how often a spread could be proposed at all, and what it
was worth 5 and 10 minutes later after REAL closing costs.

Read-only: it reads `zero_dte_observations` and writes nothing, places nothing.

How to read it
  * "proposed" is how often the proposal logic found a tradeable, plausible quote. The rest
    were refused (wide, stale, cheap, rich...) and the top reasons are listed.
  * net per share = natural closing credit at the mark minus the natural debit at the entry:
    the result of paying the worst plausible price in and taking the worst plausible price out.
    Multiply by 100 for dollars per contract.
  * cost per share = (natural entry - mid entry) + (mid exit - natural exit): what crossing the
    spread twice took away. If that is as large as the typical move, nothing can pay for it.
  * "both directions" averages the bullish and bearish spread at the same moment. A rule that
    picks a direction can only be called skilled if it beats that, after costs.

What it will NOT do: say a rule works. With fewer than MIN_N marks in a cell it prints
INSUFFICIENT DATA instead of a mean, and even with enough it reports a mean and a hit rate, not
a verdict.

Usage (on the server, inside the backend container):
    python scripts/zero_dte_recorder_report.py [--days 30]
"""
from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any, Iterable, Optional

sys.path.insert(0, "/app")

MIN_N = 30
MARK_MINUTES = (5, 10)


def mark_for(row: Any, minutes: int) -> Optional[dict]:
    for m in row.get("marks") or []:
        if m.get("minutes") == minutes:
            return m
    return None


def net_per_share(row: Any, m: dict) -> Optional[float]:
    """Natural closing credit less natural entry debit, per share; None when either is unreadable."""
    if not m or not m.get("quote_ok") or m.get("natural_credit") is None or row.get("natural_debit") is None:
        return None
    return m["natural_credit"] - row["natural_debit"]


def cost_per_share(row: Any, m: dict) -> Optional[float]:
    if not m or not m.get("quote_ok") or m.get("natural_credit") is None or m.get("mid_credit") is None:
        return None
    if row.get("natural_debit") is None or row.get("mid_debit") is None:
        return None
    return (row["natural_debit"] - row["mid_debit"]) + (m["mid_credit"] - m["natural_credit"])


def stats_for(values: list[float]) -> dict:
    n = len(values)
    if n < MIN_N:
        return {"n": n, "verdict": f"INSUFFICIENT DATA (n={n}, need {MIN_N})"}
    return {"n": n, "mean": statistics.fmean(values), "median": statistics.median(values),
            "hit": sum(1 for v in values if v > 0) / n, "verdict": None}


def summarize(rows: Iterable[dict]) -> dict:
    """Pure aggregation over observation dicts, so it can be tested without a database."""
    rows = list(rows)
    out: dict[str, Any] = {"observations": len(rows)}
    out["proposed"] = sum(1 for r in rows if r["proposed"])
    reasons = Counter()
    for r in rows:
        if not r["proposed"]:
            for x in r.get("refusal_reasons") or []:
                reasons[x.split(":")[0][:60] if x.startswith("chain unavailable") else x[:70]] += 1
    out["top_refusals"] = reasons.most_common(5)

    expected = {mm: 0 for mm in MARK_MINUTES}
    got = {mm: 0 for mm in MARK_MINUTES}
    readable = {mm: 0 for mm in MARK_MINUTES}
    by_key: dict[tuple, list[float]] = defaultdict(list)
    costs: dict[tuple, list[float]] = defaultdict(list)
    both: dict[tuple, dict[tuple, list[float]]] = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if not r["proposed"]:
            continue
        for mm in MARK_MINUTES:
            expected[mm] += 1
            m = mark_for(r, mm)
            if m is None:
                continue
            got[mm] += 1
            net = net_per_share(r, m)
            if net is None:
                continue
            readable[mm] += 1
            by_key[(mm, r["slot"], r["direction"])].append(net)
            by_key[(mm, "all", r["direction"])].append(net)
            c = cost_per_share(r, m)
            if c is not None:
                costs[(mm, "all", r["direction"])].append(c)
            both[(mm, r["slot"])][(r["trade_date"], r["symbol"])].append(net)
            both[(mm, "all")][(r["trade_date"], r["symbol"], r["slot"])].append(net)
    out["marks"] = {mm: {"expected": expected[mm], "recorded": got[mm], "readable": readable[mm]}
                    for mm in MARK_MINUTES}
    out["net"] = {k: stats_for(v) for k, v in sorted(by_key.items(), key=lambda kv: str(kv[0]))}
    out["cost"] = {k: (statistics.fmean(v) if v else None) for k, v in sorted(costs.items(), key=lambda kv: str(kv[0]))}
    # Both directions at the same moment: the random-direction baseline.
    base: dict[tuple, list[float]] = {}
    for (mm, slot), groups in both.items():
        vals = [statistics.fmean(v) for v in groups.values() if len(v) == 2]
        base[(mm, slot, "both directions")] = vals
    out["baseline"] = {k: stats_for(v) for k, v in sorted(base.items(), key=lambda kv: str(kv[0]))}
    return out


def render(s: dict, days: int) -> str:
    lines = [f"0DTE recorder, last {days} days: {s['observations']} observations, "
             f"{s['proposed']} proposed ({(s['proposed'] / s['observations'] * 100 if s['observations'] else 0):.0f}%)"]
    if s["top_refusals"]:
        lines.append("Top refusal reasons:")
        lines += [f"  {n:>4}  {r}" for r, n in s["top_refusals"]]
    for mm, c in s["marks"].items():
        lines.append(f"{mm}-minute marks: {c['recorded']} of {c['expected']} recorded, {c['readable']} readable")
    lines.append("\nNet result per share after real costs (x100 = dollars per contract):")

    def fmt(v):
        return v["verdict"] if v["verdict"] else \
            f"n={v['n']}  mean {v['mean']:+.3f}  median {v['median']:+.3f}  win rate {v['hit']:.0%}"
    for k, v in s["net"].items():
        if k[1] == "all":
            cost = s["cost"].get(k)
            extra = f"   avg cost {cost:.3f}" if cost is not None else ""
            lines.append(f"  {k[0]:>2} min  {k[2]:<8}  {fmt(v)}{extra}")
    lines.append("\nBoth directions at the same moment (the random-direction baseline):")
    for k, v in s["baseline"].items():
        if k[1] == "all":
            lines.append(f"  {k[0]:>2} min  {fmt(v)}")
    lines.append("\nThis is a measurement, not a verdict: no rule is endorsed by a mean above.")
    return "\n".join(lines)


async def main(days: int) -> None:
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.zero_dte_observation import ZeroDteObservation as Obs

    since = date.today() - timedelta(days=days)
    async with AsyncSessionLocal() as session:
        recs = (await session.execute(select(Obs).where(Obs.trade_date >= since))).scalars().all()
    rows = [{"trade_date": r.trade_date, "slot": r.slot, "symbol": r.symbol, "direction": r.direction,
             "proposed": r.proposed, "refusal_reasons": r.refusal_reasons, "mid_debit": r.mid_debit,
             "natural_debit": r.natural_debit, "marks": r.marks} for r in recs]
    print(render(summarize(rows), days))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    asyncio.run(main(args.days))
