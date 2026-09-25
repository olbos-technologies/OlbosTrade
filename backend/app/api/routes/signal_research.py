"""
Signal research API — forward-return study over every tracked signal.

Equity-only by default; pass ``asset_type=crypto`` or ``asset_type=all``. See
_load_outcomes() for why the default is not "all".

Answers: of the signals this system generated, how many actually moved to
target vs stop, and how many trading days did that take. Complements Mode
Analytics (which only covers the handful of trades that got executed) with
a much larger, unbiased sample: every routable signal, traded or not.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

router = APIRouter()


async def _load_outcomes(asset_type: str = "equity") -> list[dict]:
    """
    Load tracked outcomes for ONE asset class, defaulting to equity.

    The default is the whole point. This endpoint previously selected every row
    in signal_outcomes, which was the same thing as "every equity signal" for as
    long as equity was the only producer. Now that the crypto scan writes here
    too (crypto_scan.py), an unfiltered select would silently blend two
    populations whose confidence numbers are NOT comparable — crypto is scored
    with no orderflow input at all, so it clears a given confidence on less
    evidence. A pooled hit rate across the two would look like a result and
    measure nothing.

    ``asset_type="all"`` pools them anyway, for whoever explicitly wants that.
    """
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal
    from app.models.signal_outcome import SignalOutcome

    stmt = select(SignalOutcome)
    if asset_type and asset_type != "all":
        stmt = stmt.where(SignalOutcome.asset_type == asset_type)

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(stmt)).scalars().all()

    return [
        {
            "id":                str(r.id),
            "ticker":            r.ticker,
            "asset_type":        r.asset_type,
            "action":            r.action,
            "confidence":        float(r.confidence),
            "status":            r.status,
            "generated_at":      r.generated_at.isoformat() if r.generated_at else None,
            "resolved_at":       r.resolved_at.isoformat() if r.resolved_at else None,
            "days_to_resolve":   r.days_to_resolve,
            "entry_price":       float(r.entry_price),
            "target_price":      float(r.target_price),
            "stop_price":        float(r.stop_price),
            "exit_price":        float(r.exit_price) if r.exit_price is not None else None,
            "target_move_pct":   float(r.target_move_pct) if r.target_move_pct is not None else None,
            "max_favorable_pct": float(r.max_favorable_pct) if r.max_favorable_pct is not None else None,
            "max_adverse_pct":   float(r.max_adverse_pct) if r.max_adverse_pct is not None else None,
            # Without these, _is_uncensored() is False for every row this
            # endpoint serves, so _mfe_r silently falls back to the censored
            # excursion and _censoring_ceiling_r never lifts — the uncensoring
            # work would be invisible here while looking fine in the database.
            # Raised in review on PR #65.
            "mfe_full_pct":      float(r.mfe_full_pct) if r.mfe_full_pct is not None else None,
            "mae_full_pct":      float(r.mae_full_pct) if r.mae_full_pct is not None else None,
            "full_window_days":  r.full_window_days,
            "regime":            r.regime,
        }
        for r in rows
    ]


@router.get("/outcomes")
async def get_signal_outcomes(
    regime: Optional[str] = Query(None),
    asset_type: str = Query("equity", description="equity | crypto | all"),
):
    """
    Hit rate / days-to-resolve breakdown across every tracked signal of one
    asset class — not just the ones that were traded. See
    signal_outcome_tracker.py.

    ``asset_type`` defaults to equity; "crypto" and "all" are the alternatives.
    ``regime`` filters to one market regime. Omit it to also get a
    ``by_regime`` breakdown alongside the unfiltered ``by_confidence_bucket``.
    """
    from app.services.signal_outcome_tracker import compute_signal_outcome_stats

    outcomes = await _load_outcomes(asset_type)
    if regime:
        outcomes = [o for o in outcomes if o["regime"] == regime]
    if not outcomes:
        return {
            "message": (
                f"No {asset_type} signals tracked yet. Outcomes are recorded "
                "automatically as the scanners run and resolved every 6 hours "
                "against fresh daily bars."
            ),
            "asset_type": asset_type,
            "total": 0,
        }
    return {**compute_signal_outcome_stats(outcomes), "asset_type": asset_type}


@router.get("/outcomes/raw")
async def get_signal_outcomes_raw(
    limit: int = Query(200, le=1000),
    status: Optional[str] = Query(None, description="pending | target_hit | stop_hit | expired"),
    asset_type: str = Query("equity", description="equity | crypto | all"),
):
    """Raw per-signal outcome rows, most recent first."""
    outcomes = await _load_outcomes(asset_type)
    if status:
        outcomes = [o for o in outcomes if o["status"] == status]
    outcomes.sort(key=lambda o: o["generated_at"] or "", reverse=True)
    return {"outcomes": outcomes[:limit], "total": len(outcomes),
            "asset_type": asset_type}
