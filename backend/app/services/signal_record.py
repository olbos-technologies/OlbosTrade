"""
Signal record: how recorded signals actually turned out, per style and group.

Pure functions over plain rows from `signal_outcomes`, so the definitions are
pinned by tests and the same code can feed a page later (audit phase 2, see
docs/SIGNAL_QUALITY_AUDIT.md). No I/O, no writes.

Rules, in order:
  1. One signal per (ticker, asset_type, action, UTC day). Duplicates are
     collapsed in memory with the SAME survivor rule as
     scripts/dedupe_signal_outcomes.py (a resolved row beats a pending one, then
     the earliest wins). Nothing is deleted here; this only stops duplicates from
     inflating the sample.
  2. A result is measured in R, the multiple of the risk the signal defined:
     risk = |entry - stop|; result = direction x (exit - entry) / risk.
     target_hit and stop_hit use the recorded exit, or the target/stop price
     when no exit was stored; expired uses the recorded exit and is skipped
     (and counted) when there is none.
  3. Costs: a round-trip allowance in basis points of the entry price,
     converted to R and subtracted. Defaults are assumptions, set per style,
     and printed with every report.
  4. Win rate = share of results above zero AFTER costs, with a Wilson 95%
     interval. Target-hit rate is reported separately.
  5. Expectancy = mean net R, with a 95% interval from a bootstrap that
     resamples whole trading DAYS, because signals on one day move together.
  6. Verdict, for the publication standard: "insufficient sample" below the
     minimum count; otherwise "positive" when the whole expectancy interval is
     above zero, "negative" when it is below, else "unproven".
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import datetime
from typing import Callable, Iterable, Optional

STATUS_RANK = {"target_hit": 3, "stop_hit": 3, "expired": 2, "pending": 1}
RESOLVED = ("target_hit", "stop_hit", "expired")

# Round-trip cost allowances in basis points of entry. ASSUMPTIONS, printed with
# every report and overridable; they are not measured fills.
DEFAULT_COST_BPS = {"equity": 10.0, "crypto": 30.0}
FALLBACK_COST_BPS = 20.0
MIN_N = 300


def _f(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def keep_rank(row: dict) -> tuple:
    """Survivor key, identical in effect to scripts/dedupe_signal_outcomes.py."""
    return (STATUS_RANK.get(row.get("status"), 0), -row["generated_at"].timestamp())


def dedupe(rows: Iterable[dict]) -> tuple[list[dict], int]:
    """One row per (ticker, asset_type, action, UTC day). Returns (rows, duplicates dropped)."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        g = r["generated_at"]
        groups[(r.get("ticker"), r.get("asset_type"), r.get("action"), g.date())].append(r)
    kept = [max(m, key=keep_rank) for m in groups.values()]
    dropped = sum(len(m) - 1 for m in groups.values())
    return kept, dropped


def r_result(row: dict, cost_bps: float) -> Optional[dict]:
    """Gross and net R for one resolved row, or None when it cannot be measured."""
    status = row.get("status")
    if status not in RESOLVED:
        return None
    entry, stop = _f(row.get("entry_price")), _f(row.get("stop_price"))
    if entry is None or stop is None or entry <= 0:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    direction = {"BUY": 1.0, "SELL": -1.0}.get(str(row.get("action", "")).upper())
    if direction is None:
        return None
    exit_ = _f(row.get("exit_price"))
    if exit_ is None:
        exit_ = {"target_hit": _f(row.get("target_price")), "stop_hit": stop}.get(status)
    if exit_ is None:
        return None
    gross = direction * (exit_ - entry) / risk
    cost = (cost_bps / 10_000.0) * entry / risk
    return {"gross_r": gross, "net_r": gross - cost, "cost_r": cost}


def wilson(k: int, n: int, z: float = 1.96) -> Optional[tuple[float, float]]:
    if n <= 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def clustered_mean_ci(values: list[float], days: list[str], draws: int = 2000,
                      rng: Optional[random.Random] = None) -> Optional[tuple[float, float]]:
    """95% interval for the mean, resampling whole days with replacement."""
    if len(values) < 2:
        return None
    by_day: dict[str, list[float]] = defaultdict(list)
    for v, d in zip(values, days):
        by_day[d].append(v)
    keys = sorted(by_day)
    if len(keys) < 2:
        return None
    rng = rng or random.Random(20261119)
    means = []
    for _ in range(draws):
        tot = cnt = 0.0
        for _k in range(len(keys)):
            vs = by_day[keys[rng.randrange(len(keys))]]
            tot += sum(vs)
            cnt += len(vs)
        means.append(tot / cnt)
    means.sort()
    return (means[int(0.025 * draws)], means[int(0.975 * draws) - 1])


def verdict(n: int, ci: Optional[tuple[float, float]], min_n: int = MIN_N) -> str:
    if n < min_n or ci is None:
        return "insufficient sample"
    if ci[0] > 0:
        return "positive"
    if ci[1] < 0:
        return "negative"
    return "unproven"


def confidence_bucket(conf) -> str:
    c = _f(conf)
    if c is None:
        return "none"
    for lo in (0.9, 0.8, 0.7, 0.6, 0.5):
        if c >= lo:
            return f">= {lo:.1f}"
    return "< 0.5"


def summarize(rows: list[dict], key: Callable[[dict], tuple], cost_bps: dict[str, float] | None = None,
              min_n: int = MIN_N, draws: int = 2000, seed: int = 20261119) -> list[dict]:
    """One summary per group. `rows` should already be deduped."""
    cost_bps = {**DEFAULT_COST_BPS, **(cost_bps or {})}
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    out = []
    for gk in sorted(groups, key=lambda k: tuple(str(x) for x in k)):
        members = groups[gk]
        pending = sum(1 for r in members if r.get("status") == "pending")
        measured, unmeasurable = [], 0
        for r in members:
            if r.get("status") not in RESOLVED:
                continue
            res = r_result(r, cost_bps.get(r.get("asset_type"), FALLBACK_COST_BPS))
            if res is None:
                unmeasurable += 1
                continue
            measured.append((r, res))
        n = len(measured)
        nets = [m[1]["net_r"] for m in measured]
        days = [m[0]["generated_at"].date().isoformat() for m in measured]
        wins = [x for x in nets if x > 0]
        losses = [x for x in nets if x <= 0]
        hits = sum(1 for m in measured if m[0]["status"] == "target_hit")
        ci = clustered_mean_ci(nets, days, draws=draws, rng=random.Random(seed))
        out.append({
            "group": list(gk),
            "signals": len(members),
            "pending": pending,
            "resolved": n,
            "unmeasurable": unmeasurable,
            "days": len(set(days)),
            "target_hit_rate": hits / n if n else None,
            "win_rate": len(wins) / n if n else None,
            "win_rate_ci": wilson(len(wins), n),
            "avg_win_r": sum(wins) / len(wins) if wins else None,
            "avg_loss_r": sum(losses) / len(losses) if losses else None,
            "expectancy_r": sum(nets) / n if n else None,
            "expectancy_ci": ci,
            "avg_cost_r": sum(m[1]["cost_r"] for m in measured) / n if n else None,
            "verdict": verdict(n, ci, min_n),
        })
    return out


def meets_standard(summary: dict, min_win_rate_lb: Optional[float] = None) -> bool:
    """The proposed publication gate: a positive verdict, and optionally a win-rate lower bound."""
    if summary.get("verdict") != "positive":
        return False
    if min_win_rate_lb is not None:
        ci = summary.get("win_rate_ci")
        return bool(ci) and ci[0] >= min_win_rate_lb
    return True


def as_row(raw) -> dict:
    """A DB row (mapping or object) as the plain dict these functions expect."""
    get = raw.get if isinstance(raw, dict) else (lambda k: getattr(raw, k, None))
    g = get("generated_at")
    if isinstance(g, str):
        g = datetime.fromisoformat(g)
    return {k: get(k) for k in ("ticker", "asset_type", "action", "confidence", "entry_price", "stop_price",
                                "target_price", "exit_price", "status", "regime", "signal_engine_version")} | {"generated_at": g}
