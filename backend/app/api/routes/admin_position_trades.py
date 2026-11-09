"""Operator view: each broker position next to the trade rows recorded for it.

GET /api/admin/position-trades

Answers "why does the Positions screen say UNKNOWN for this holding?" and "is every
holding accounted for?" without a database shell. It reads across owners, which the
Positions screen does not, so it is for the operator only: a signed-in caller whose
scope is the operator's book. Anyone else gets a 403, and a missing identity a 403
too. Read only: it places, cancels and writes nothing.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.api.routes.admin_claims import _operator
from app.api.scope_deps import scope_or_401
from app.core.database import AsyncSessionLocal
from app.services.position_trade_report import load_report
from app.services.reconciliation_entry_gate import scope_label
from app.services.trade_scope import designated_operator_id
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin/position-trades", tags=["admin"])


@router.get("")
async def position_trades(request: Request) -> dict:
    _operator(request)                      # 403 without an identity
    operator = designated_operator_id()
    async with AsyncSessionLocal() as session:
        viewer = await scope_or_401(session, request)
    if viewer != operator:
        raise HTTPException(403, "This view reads across owners and is for the operator only.")
    report = await load_report(viewer, operator)
    return {"viewer_scope": scope_label(viewer), "operator_scope": scope_label(operator), **report}
