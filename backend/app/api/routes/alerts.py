"""
Smart Alerts + Notification Center routes.

  /api/alerts/rules        CRUD alert rules
  /api/alerts/evaluate     evaluate a symbol snapshot (fires create notifications)
  /api/notifications       list / unread-count / mark-read

Alerts create notifications or gated candidates only — never orders.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import require_api_key
from app.api.rate_limit import rate_limit
from app.services.alerts import service as svc

router = APIRouter()            # mounted at /api/alerts
notif_router = APIRouter()     # mounted at /api/notifications


class PredicateModel(BaseModel):
    metric: str
    op: str
    value: object | None = None


class CreateRule(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    symbol: str = Field(min_length=1, max_length=16)
    predicates: list[PredicateModel]
    mode: str = "manual"
    cooldown_min: int = 60


class EvaluateBody(BaseModel):
    symbol: str
    snapshot: dict


# ── Alert rules ──────────────────────────────────────────────────────────────
@router.get("/rules")
async def list_rules():
    return {"rules": await svc.list_rules()}


@router.post("/rules", dependencies=[Depends(require_api_key), Depends(rate_limit)])
async def create_rule(body: CreateRule):
    return await svc.create_rule(
        name=body.name, symbol=body.symbol,
        predicates=[p.model_dump() for p in body.predicates],
        mode=body.mode, cooldown_min=body.cooldown_min,
    )


@router.post("/rules/{rule_id}/toggle", dependencies=[Depends(require_api_key)])
async def toggle_rule(rule_id: str, enabled: bool = True):
    await svc.toggle_rule(rule_id, enabled)
    return {"ok": True}


@router.delete("/rules/{rule_id}", dependencies=[Depends(require_api_key)])
async def delete_rule(rule_id: str):
    if not await svc.delete_rule(rule_id):
        raise HTTPException(404, "Rule not found")
    return {"deleted": True}


@router.post("/evaluate")
async def evaluate(body: EvaluateBody):
    """Evaluate a symbol against its rules with a supplied metrics snapshot.
    Fires create notifications only; the response documents that no order is made."""
    outcomes = await svc.evaluate_symbol(body.symbol, body.snapshot)
    return {"symbol": body.symbol.upper(), "outcomes": outcomes, "orders_created": 0}


# ── Notifications ────────────────────────────────────────────────────────────
@notif_router.get("")
async def notifications(limit: int = 50, unread_only: bool = False):
    return {
        "notifications": await svc.list_notifications(limit=limit, unread_only=unread_only),
        "unread": await svc.unread_count(),
    }


@notif_router.get("/unread-count")
async def unread():
    return {"unread": await svc.unread_count()}


@notif_router.post("/{notification_id}/read")
async def read_one(notification_id: str):
    await svc.mark_read(notification_id)
    return {"ok": True}


@notif_router.post("/read-all")
async def read_all():
    return {"marked": await svc.mark_all_read()}
