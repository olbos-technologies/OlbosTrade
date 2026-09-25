"""
Freeze the day's top 3 BUY and top 3 SELL signals at a fixed decision time.

Runs once per trading day at 10:00 ET — thirty minutes after the open, so the
scanner has completed a full pass and the ranking reflects a settled book
rather than the first tick of the session.

**Ranked by `opportunity_score`**, because that is the composite the desk
itself uses to prioritise. Ranking by a metric invented for the display would
record something the system never acted on.

**Deduplicated to each signal's earliest appearance that day.** The scanner
re-records the same (ticker, action, day) roughly 45 times as it re-scores, so
"the signal" has to mean one thing. The first appearance is the honest choice:
it is the moment the operator could first have acted, and it cannot be
inflated by a later re-score that already knows how the morning went.

**Immutable.** A date that already has rows is never rewritten — the capture
returns `already_captured` instead. The unique constraint on
(trade_date, action, rank) enforces that at the schema level too, so a race
between two callers fails loudly rather than duplicating a day.

Reads `signal_outcomes` rather than the scanner's in-memory store: that store
is wiped on restart, and a record meant to outlive the process cannot depend
on it. It also makes the capture reproducible from the database alone.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

TOP_N = 3
BUY, SELL = "BUY", "SELL"


def _et_trade_date(now: Optional[datetime] = None) -> date:
    """The ET calendar date. A trading day is a day in market time, not in
    whatever timezone the container happens to run in (it is Etc/UTC)."""
    now = now or datetime.now(timezone.utc)
    # ET is UTC-4 in DST, UTC-5 otherwise. The capture runs at 10:00 ET, far
    # from any date boundary, so a fixed -5 is safe for date attribution in
    # both — 10:00 ET is 14:00 or 15:00 UTC, and neither crosses midnight.
    return (now.astimezone(timezone.utc) - timedelta(hours=5)).date()


def rank_candidates(rows: list[dict], top_n: int = TOP_N) -> dict[str, list[dict]]:
    """Pure ranking: dedupe to earliest appearance per (ticker, action), then
    take the top `top_n` per side by opportunity_score.

    Rows without an opportunity_score are excluded rather than sorted as zero.
    A missing score is not a low score, and a snapshot is a record of what the
    desk ranked — a signal it never scored was never ranked.
    """
    earliest: dict[tuple[str, str], dict] = {}
    for r in rows:
        ticker = (r.get("ticker") or "").upper()
        action = (r.get("action") or "").upper()
        if not ticker or action not in (BUY, SELL):
            continue
        if r.get("opportunity_score") is None:
            continue
        key = (ticker, action)
        prior = earliest.get(key)
        if prior is None or (r.get("generated_at") and prior.get("generated_at")
                             and r["generated_at"] < prior["generated_at"]):
            earliest[key] = r

    out: dict[str, list[dict]] = {}
    for action in (BUY, SELL):
        side = [r for (t, a), r in earliest.items() if a == action]
        side.sort(key=lambda r: (-(r.get("opportunity_score") or 0),
                                 (r.get("ticker") or "")))
        out[action] = side[:top_n]
    return out


async def capture_daily_snapshot(
    trade_date: Optional[date] = None, *, force: bool = False
) -> dict[str, Any]:
    """Freeze today's top 3 per side. Idempotent; never rewrites a day."""
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.daily_signal_snapshot import DailySignalSnapshot
    from app.models.signal_outcome import SignalOutcome

    day = trade_date or _et_trade_date()
    report: dict[str, Any] = {"trade_date": day.isoformat(), "captured": 0}

    async with AsyncSessionLocal() as session:
        existing = (await session.execute(
            select(DailySignalSnapshot).where(DailySignalSnapshot.trade_date == day)
        )).scalars().all()
        if existing and not force:
            return {**report, "status": "already_captured",
                    "captured": len(existing)}

        # Signals generated on this ET date. The window is generous on both
        # sides of the ET day so nothing is lost to the UTC offset.
        start = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        rows = (await session.execute(
            select(SignalOutcome).where(
                # Equity only. The crypto scan writes to the same table now
                # (crypto_scan.py), and without this filter the daily snapshot's
                # top-N candidate ranking would quietly start competing crypto
                # against equities — two populations whose confidence is not
                # comparable, ranked against each other as though it were.
                SignalOutcome.asset_type == "equity",
                SignalOutcome.generated_at >= start,
                SignalOutcome.generated_at < start + timedelta(days=2),
            )
        )).scalars().all()

        candidates = [{
            "ticker": r.ticker,
            "action": r.action,
            "opportunity_score": r.opportunity_score,
            "confidence": r.confidence,
            "entry_price": r.entry_price,
            "stop_price": r.stop_price,
            "target_price": r.target_price,
            "regime": r.regime,
            "generated_at": r.generated_at,
            "signal_id": r.signal_id,
            "signal_outcome_id": r.id,
        } for r in rows if r.generated_at and _et_trade_date(r.generated_at) == day]

        ranked = rank_candidates(candidates)
        if not ranked[BUY] and not ranked[SELL]:
            # A day with no scored signals is a real outcome, not an error —
            # a weekend, a holiday, or a session the scanner sat out. Recording
            # nothing is correct; inventing a row would not be.
            return {**report, "status": "no_scored_signals"}

        written = 0
        for action in (BUY, SELL):
            for i, c in enumerate(ranked[action], start=1):
                session.add(DailySignalSnapshot(
                    trade_date=day, action=action, rank=i,
                    ticker=c["ticker"],
                    signal_outcome_id=c["signal_outcome_id"],
                    signal_id=c["signal_id"],
                    opportunity_score=c["opportunity_score"],
                    confidence=c["confidence"],
                    entry_price=c["entry_price"],
                    stop_price=c["stop_price"],
                    target_price=c["target_price"],
                    regime=c["regime"],
                    generated_at=c["generated_at"],
                ))
                written += 1
        await session.commit()

    logger.info(
        "daily snapshot %s: froze %d (%d buy, %d sell) — %s",
        day, written, len(ranked[BUY]), len(ranked[SELL]),
        ", ".join(f"{c['ticker']}:{c['opportunity_score']}"
                  for c in ranked[BUY] + ranked[SELL]),
    )
    return {**report, "status": "ok", "captured": written,
            "buy": [c["ticker"] for c in ranked[BUY]],
            "sell": [c["ticker"] for c in ranked[SELL]]}
