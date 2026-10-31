"""
Where did the money actually come from, and which strategy earned it?

Run where the data lives:

    cd /opt/olbostrade
    ( set -a; . backend/.env.prod; set +a; \
      docker compose -f docker-compose.hetzner.yml exec backend \
      python scripts/trade_attribution_report.py --days 120 )

Read-only. No writes, no orders, no broker calls. Prints no credentials.

WHAT THIS IS FOR
----------------
After three months of trading the obvious question is "which strategy is
best". The obvious analysis — group by strategy, sort by total P&L, trade the
winner — is how a book gets destroyed, because at this sample size the ranking
is mostly noise. This repository already has direct evidence of that: the
equity exit sweep found an optimal stop/target geometry on six months of data,
and the twelve-month window INVERTED the ranking; a 9-month-train /
3-month-validate run returned an out-of-sample Sharpe gain of -0.59. The
in-sample winner was not merely weaker out of sample, it was worse than the
baseline it replaced.

So this report is built to resist that failure rather than repeat it:

  * Every mean carries a 95% confidence interval from a DATE-CLUSTERED
    bootstrap. Trades opened on the same day share a market; treating them as
    independent is what makes a noise ranking look significant. The same
    clustering is used by confidence_skill_test.py and the gap study on this
    universe.
  * Cells below MIN_CELL closed trades are printed but marked NOT
    INTERPRETABLE, and are excluded from every ranking.
  * Strategies are only ranked when their intervals do not overlap. Where they
    overlap, the report says the data cannot separate them. That is a finding,
    not a failure to produce one.
  * The headline question is asked first and plainly: is total P&L
    distinguishable from zero at all? If it is not, no per-strategy breakdown
    below it means anything, and the report says so.

WHAT EACH SECTION IMPLIES
-------------------------
  1 COVERAGE          Sample size and span. Everything downstream is
                      conditioned on this, so it is printed before any result.
  2 HEADLINE          Total P&L with an interval. If it spans 0, the system's
                      edge is not yet measurable and sections 3-8 are
                      descriptive only.
  3 BY STRATEGY       The question asked. Win rate alone is a trap: a 90% win
                      rate at -5R on the loser is a losing strategy, so mean
                      P&L, profit factor and mean R are shown together.
  4 BY EXIT REASON    Where money leaves. Losses concentrated in `expiration`
                      means positions are being held to the end rather than
                      managed; concentrated in `stop_loss` means the stop is
                      inside the noise; concentrated in `manual` means a human
                      is overriding the system, and the system's record is not
                      the system's.
  5 BY REGIME         Whether the edge is conditional. A strategy that is
                      profitable in one regime and not another is a routing
                      problem, not a strategy problem.
  6 CAPTURE           pnl_capture_pct = realised P&L / MFE. Low capture with
                      healthy MFE means winners are given back before exit.
                      Read WITH the exit sweep result above: if capture is
                      poor but geometry tuning does not survive out of sample,
                      the exits are not the binding constraint.
  7 COST DRAG         Commission as a share of gross P&L. On defined-risk
                      spreads this is routinely the difference between a thin
                      edge and no edge, and it is the one number that is not a
                      forecast.
  8 SCORE SKILL       AUC of signal_score over winners vs losers, at TRADE
                      level. 0.5 is a coin flip. This is the deepest question
                      in the report: if the score that gates routing cannot
                      separate a winning trade from a losing one, then no
                      amount of exit tuning, sizing, or strategy selection
                      will help, because there is nothing to select on.

A note on multiplicity: this report tests several strategies across several
regimes. Running enough cells guarantees one looks significant. Treat a single
flagged cell among many as a hypothesis to test forward, never as a result to
trade.
"""

import argparse
import asyncio
import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app")

from sqlalchemy import select  # noqa: E402

from app.core.database import AsyncSessionLocal  # noqa: E402
from app.models.trade import Trade  # noqa: E402

MIN_CELL = 30          # below this, a cell is reported but never ranked
BOOTSTRAP_REPS = 4000
SEED = 4242


def _f(value) -> float:
    """Decimal | None -> float, with None as 0.0 only where the caller means it."""
    return 0.0 if value is None else float(value)


def clustered_bootstrap_mean(
    rows: list[tuple[str, float]], reps: int = BOOTSTRAP_REPS,
) -> tuple[float, float, float]:
    """
    Mean and a 95% CI, resampling DAYS rather than trades.

    Trades opened on the same day are not independent draws — they share one
    market. Resampling trades individually understates the interval, which is
    exactly how a noise ranking acquires significance.
    """
    if not rows:
        return 0.0, 0.0, 0.0
    by_day: dict[str, list[float]] = defaultdict(list)
    for day, value in rows:
        by_day[day].append(value)
    days = list(by_day)
    observed = sum(v for _, v in rows) / len(rows)
    if len(days) < 3:
        return observed, float("nan"), float("nan")

    rng = random.Random(SEED)
    means = []
    for _ in range(reps):
        drawn = [by_day[rng.choice(days)] for _ in days]
        flat = [v for group in drawn for v in group]
        if flat:
            means.append(sum(flat) / len(flat))
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[min(int(0.975 * len(means)), len(means) - 1)]
    return observed, lo, hi


def auc(scored_wins: list[float], scored_losses: list[float]) -> float:
    """P(a random winner scored higher than a random loser). 0.5 is a coin flip."""
    if not scored_wins or not scored_losses:
        return float("nan")
    wins = 0.0
    for w in scored_wins:
        for l in scored_losses:
            wins += 1.0 if w > l else (0.5 if w == l else 0.0)
    return wins / (len(scored_wins) * len(scored_losses))


def profit_factor(values: list[float]) -> float:
    gains = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v < 0)
    if losses == 0:
        return float("inf") if gains > 0 else float("nan")
    return gains / losses


def _ci(lo: float, hi: float) -> str:
    if math.isnan(lo) or math.isnan(hi):
        return "CI n/a (<3 trading days)"
    return f"[{lo:+9.2f}, {hi:+9.2f}]"


def _verdict(lo: float, hi: float) -> str:
    if math.isnan(lo):
        return "—"
    if lo > 0:
        return "positive"
    if hi < 0:
        return "NEGATIVE"
    return "spans 0"


def _cell(name: str, rows: list, width: int = 22) -> str:
    pnls = [_f(t.pnl) for t in rows]
    days = [(t.entry_date.date().isoformat(), _f(t.pnl)) for t in rows]
    n = len(rows)
    wins = sum(1 for p in pnls if p > 0)
    mean, lo, hi = clustered_bootstrap_mean(days)
    flag = "" if n >= MIN_CELL else f"   << n<{MIN_CELL}: NOT INTERPRETABLE"
    return (
        f"  {name:<{width}} n={n:<5} win={wins / n * 100 if n else 0:5.1f}%  "
        f"total={sum(pnls):+11.2f}  mean={mean:+9.2f} {_ci(lo, hi)} "
        f"{_verdict(lo, hi):<9}  PF={profit_factor(pnls):6.2f}{flag}"
    )


def _group(trades: list, key) -> dict:
    out: dict = defaultdict(list)
    for t in trades:
        out[key(t) or "(unrecorded)"].append(t)
    return dict(sorted(out.items(), key=lambda kv: -len(kv[1])))


async def main(days: int, mode: str | None) -> None:
    since = datetime.now(timezone.utc) - timedelta(days=days)

    async with AsyncSessionLocal() as session:
        stmt = select(Trade).where(
            Trade.entry_date >= since,
            Trade.status == "closed",
            Trade.pnl.isnot(None),
        )
        trades = list((await session.execute(stmt)).scalars().all())

    if mode:
        trades = [t for t in trades if (t.trading_mode_at_entry or "") == mode]

    print("=" * 100)
    print(f"TRADE ATTRIBUTION — last {days} days"
          + (f", mode={mode}" if mode else "")
          + f"   (generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC)")
    print("=" * 100)

    if not trades:
        print("\nNo closed trades with a recorded P&L in this window.")
        print("Nothing below can be computed. This is itself the finding: the")
        print("system is not producing completed trades, which is a routing or")
        print("entry-gate question, not a strategy-selection one.")
        return

    # ---------- 1 COVERAGE ----------
    first = min(t.entry_date for t in trades)
    last = max(t.exit_date or t.entry_date for t in trades)
    trading_days = len({t.entry_date.date() for t in trades})
    modes = _group(trades, lambda t: t.trading_mode_at_entry)
    print(f"\n1 COVERAGE")
    print(f"  closed trades with P&L : {len(trades)}")
    print(f"  span                   : {first:%Y-%m-%d} -> {last:%Y-%m-%d}")
    print(f"  distinct entry days    : {trading_days}")
    print(f"  underlyings            : {len({t.underlying for t in trades})}")
    for name, rows in modes.items():
        print(f"  mode {name:<18}: {len(rows)}")
    if len(trades) < MIN_CELL:
        print(f"\n  !! Fewer than {MIN_CELL} closed trades in total. Every number below is")
        print( "     description, not evidence. Do not change strategy allocation on it.")

    # ---------- 2 HEADLINE ----------
    all_pnl = [_f(t.pnl) for t in trades]
    mean, lo, hi = clustered_bootstrap_mean(
        [(t.entry_date.date().isoformat(), _f(t.pnl)) for t in trades]
    )
    print(f"\n2 HEADLINE — is there an edge at all?")
    print(f"  total P&L              : {sum(all_pnl):+.2f}")
    print(f"  mean per trade         : {mean:+.2f}  {_ci(lo, hi)}")
    print(f"  win rate               : {sum(1 for p in all_pnl if p > 0) / len(all_pnl) * 100:.1f}%")
    print(f"  profit factor          : {profit_factor(all_pnl):.2f}")
    if math.isnan(lo):
        print("  VERDICT                : too few trading days to interval at all.")
    elif lo <= 0 <= hi:
        print("  VERDICT                : mean per-trade P&L SPANS ZERO. At this sample")
        print("                           size the system's edge is not distinguishable")
        print("                           from no edge. Sections 3-8 are descriptive;")
        print("                           do not rank strategies on them.")
    elif hi < 0:
        print("  VERDICT                : mean per-trade P&L is NEGATIVE with 95%")
        print("                           confidence. Position sizing and trade frequency")
        print("                           are the levers that matter before strategy mix.")
    else:
        print("  VERDICT                : mean per-trade P&L is positive with 95%")
        print("                           confidence. Now the breakdown below is worth reading.")

    # ---------- 3-5 BREAKDOWNS ----------
    for title, note, key in (
        ("3 BY STRATEGY", "the question asked", lambda t: t.strategy),
        ("4 BY EXIT REASON", "where money leaves", lambda t: t.exit_reason),
        ("5 BY REGIME", "is the edge conditional?", lambda t: t.regime),
    ):
        print(f"\n{title} — {note}")
        groups = _group(trades, key)
        for name, rows in groups.items():
            print(_cell(name, rows))
        if title.startswith("3"):
            ranked = {
                n: clustered_bootstrap_mean(
                    [(t.entry_date.date().isoformat(), _f(t.pnl)) for t in r])
                for n, r in groups.items() if len(r) >= MIN_CELL
            }
            print()
            if len(ranked) < 2:
                print(f"  Fewer than two strategies reach n>={MIN_CELL}. The data cannot rank")
                print( "  strategies against each other yet — it can only describe them.")
            else:
                separated = [
                    (a, b) for a in ranked for b in ranked
                    if a != b and ranked[a][1] > ranked[b][2]
                ]
                if separated:
                    for a, b in separated:
                        print(f"  SEPARATED: {a} beats {b} — intervals do not overlap.")
                else:
                    print("  No two strategies are separated: every interval overlaps every")
                    print("  other. Any ranking by total P&L here is noise. Do not reallocate.")

    # ---------- 6 CAPTURE ----------
    captured = [_f(t.pnl_capture_pct) for t in trades if t.pnl_capture_pct is not None]
    mfe = [_f(t.mfe_pnl) for t in trades if t.mfe_pnl is not None]
    mae = [_f(t.mae_pnl) for t in trades if t.mae_pnl is not None]
    print(f"\n6 CAPTURE — are winners given back before exit?")
    if captured:
        print(f"  mean pnl_capture_pct   : {sum(captured) / len(captured) * 100:.1f}%  (n={len(captured)})")
    else:
        print("  pnl_capture_pct recorded on 0 trades — cannot assess.")
    if mfe:
        print(f"  mean MFE               : {sum(mfe) / len(mfe):+.2f}")
    if mae:
        print(f"  mean MAE               : {sum(mae) / len(mae):+.2f}")
    print("  Read with the exit sweep: geometry tuning did NOT survive out of sample")
    print("  (Sharpe gain -0.59), so poor capture here points at entry timing rather")
    print("  than at the stop/target multiples.")

    # ---------- 7 COST DRAG ----------
    commission = sum(_f(t.commission_paid) for t in trades)
    gross = sum(all_pnl) + commission
    print(f"\n7 COST DRAG")
    print(f"  commission paid        : {commission:.2f}")
    print(f"  gross P&L before costs : {gross:+.2f}")
    if gross > 0:
        print(f"  commission as % of gross: {commission / gross * 100:.1f}%")
        if commission / gross > 0.3:
            print("  !! Costs are eating more than 30% of gross. On defined-risk spreads this")
            print("     is usually fixable with fewer, larger positions — a sizing change, not")
            print("     a strategy change, and the cheapest edge in this report.")
    elif commission > 0:
        print("  Gross P&L is not positive, so costs are not the binding constraint;")
        print("  they are making a losing book lose faster.")

    # ---------- 8 SCORE SKILL ----------
    scored = [t for t in trades if t.signal_score is not None]
    wins = [_f(t.signal_score) for t in scored if _f(t.pnl) > 0]
    losses = [_f(t.signal_score) for t in scored if _f(t.pnl) <= 0]
    print(f"\n8 SCORE SKILL — can signal_score tell a winning trade from a losing one?")
    print(f"  trades carrying a score: {len(scored)} of {len(trades)}")
    if len(wins) < 5 or len(losses) < 5:
        print("  Too few scored winners or losers to compute an AUC.")
        print("  Note: signal_score was added after trading began, so early trades may")
        print("  legitimately carry none — check the span above before reading this as a bug.")
    else:
        a = auc(wins, losses)
        print(f"  AUC                    : {a:.3f}   (0.5 = coin flip, n_win={len(wins)}, n_loss={len(losses)})")
        if a < 0.55:
            print("  VERDICT                : the score that GATES ROUTING does not separate")
            print("                           winners from losers. This is the finding that")
            print("                           dominates every other one in this report: there")
            print("                           is nothing to select on, so raising the")
            print("                           confidence threshold trades less, not better.")
        else:
            print("  VERDICT                : the score carries signal. Raising the routing")
            print("                           threshold should improve trade quality — test it")
            print("                           forward, on paper, before changing the live gate.")

    print(f"\n{'=' * 100}")
    print("Multiplicity: several cells were tested. One flagged cell among many is a")
    print("hypothesis to test forward, not a result to trade. Nothing here authorises a")
    print("live change; validate on paper first.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trade attribution report (read-only).")
    parser.add_argument("--days", type=int, default=120, help="lookback window (default 120)")
    parser.add_argument("--mode", type=str, default=None,
                        help="filter to one trading_mode_at_entry, e.g. paper")
    args = parser.parse_args()
    asyncio.run(main(args.days, args.mode))
