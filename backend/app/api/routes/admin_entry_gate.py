"""Operator view of the reconciliation entry gate: drill it, and read its record.

The gate (services/reconciliation_entry_gate.py) only evaluates when an entry
is about to be sent, which is the wrong moment to learn how it behaves. Before
enforcing it an operator has to see it deal with a known mismatch, a broker
that has gone away, and the recovery afterwards, and none of those can be
staged against a live desk by waiting for the right entry to come along.

Two read-only routes, operator identity required (same rule and same prefix
reasoning as admin_claims: the session allowlist matches on path alone):

  GET /api/admin/entry-gate          evaluate the gate NOW, send nothing, record
                                     nothing, and say what each mode would do
  GET /api/admin/entry-gate/records  what observe/enforce have recorded

Neither can place, cancel or modify an order, change the mode, or write a row.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from app.api.routes.admin_claims import _operator
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/entry-gate", tags=["admin"])


@router.get("")
async def evaluate_now(request: Request) -> dict:
    """Run the gate's check against the live broker without sending anything.

    Uses the same evaluation an entry would, so this is the drill: disconnect
    the gateway and `available` turns false with its cause; resolve the
    mismatch or reconnect and it turns clean again. Nothing is written, so
    drilling does not pollute the record observe is building.
    """
    _ = _operator(request)

    from app.broker.broker_factory import get_broker
    from app.core.config import settings
    from app.services import reconciliation_entry_gate as gate

    assessment = await gate.evaluate_entry_reconciliation(get_broker())
    mode = gate._mode()
    return {
        "mode": mode,
        "broker": settings.broker,
        # The gate is wired into the IBKR adapter only.
        "applies_to_current_broker": settings.broker == "ibkr",
        "scope": assessment.scope,
        "clean": assessment.clean,
        "available": assessment.available,
        "unavailable_cause": assessment.unavailable_cause,
        "reasons": assessment.reasons(),
        "explained": dict(assessment.explained),
        "elapsed_ms": assessment.elapsed_ms,
        # What an entry sent right now would meet, said per mode so the
        # operator can see what enforcing WOULD do before choosing to.
        "an_entry_now_would": {
            "observe": "proceed" if assessment.clean else (
                "proceed, and record " + ("unavailable" if not assessment.available else "would_veto")
            ),
            "enforce": "proceed" if assessment.clean else f"be vetoed: {assessment.reason}",
        },
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/records")
async def records(request: Request, days: int = 7, limit: int = 50) -> dict:
    """What the gate has recorded: counts by outcome, and the most recent rows.

    A clean check writes nothing, so these are only the checks that would have
    (observe) or did (enforce) stop an entry, and the ones that could not be
    made. The counts are the evidence for deciding whether to enforce.
    """
    _ = _operator(request)
    days = max(1, min(int(days), 90))
    limit = max(1, min(int(limit), 200))

    from app.core.database import AsyncSessionLocal
    from app.models.execution_event import ExecutionEvent

    since = datetime.now(timezone.utc) - timedelta(days=days)
    async with AsyncSessionLocal() as session:
        counts = (await session.execute(
            select(ExecutionEvent.status, func.count())
            .where(ExecutionEvent.kind == "entry_gate", ExecutionEvent.created_at >= since)
            .group_by(ExecutionEvent.status)
        )).all()
        rows = (await session.execute(
            select(ExecutionEvent)
            .where(ExecutionEvent.kind == "entry_gate", ExecutionEvent.created_at >= since)
            .order_by(ExecutionEvent.created_at.desc())
            .limit(limit)
        )).scalars().all()

    return {
        "days": days,
        "counts": {status: n for status, n in counts},
        "records": [
            {
                "at": row.created_at.isoformat() if row.created_at else None,
                "status": row.status,
                "ticker": row.ticker,
                "asset_type": row.asset_type,
                "mode": (row.payload or {}).get("mode"),
                "scope": (row.payload or {}).get("scope"),
                "reasons": (row.payload or {}).get("reasons", []),
                "unavailable_cause": (row.payload or {}).get("unavailable_cause"),
                "explained": (row.payload or {}).get("explained", {}),
                "elapsed_ms": (row.payload or {}).get("elapsed_ms"),
            }
            for row in rows
        ],
    }
