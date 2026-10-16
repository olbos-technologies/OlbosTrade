"""
Pre-rotation preflight and dry-run.

Both routes are read-only against the broker: preflight inspects, dry-run
simulates. Neither places, cancels or modifies an order, and the dry-run is
gated on the preflight so it cannot report a clean rehearsal while the order
book is still in a state nobody would want to rotate from.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from starlette.requests import HTTPConnection

from fastapi import APIRouter
from pydantic import BaseModel

from app.utils.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)


@router.get("/preflight")
async def rotation_preflight():
    """The thirteen-point safety checklist. Read-only."""
    from app.broker.broker_factory import get_broker
    from app.services.rotation_preflight import run_preflight

    try:
        return await run_preflight(get_broker())
    except Exception as exc:
        # A preflight that cannot run is not a pass.
        return {
            "all_clear": False, "error": f"{type(exc).__name__}: {exc}",
            "failed": ["preflight_itself"], "unknown": [], "checks": [],
        }


class DryRunRequest(BaseModel):
    ticker: Optional[str] = None
    action: Optional[str] = "BUY"
    confidence: Optional[float] = None
    alpha_edge_score: Optional[float] = None
    entry_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    shares: Optional[int] = None
    volume_ratio: Optional[float] = None
    # Escape hatch for rehearsing the comparison while preflight is red. It
    # cannot cause an order — nothing in this route submits — but it must be
    # asked for explicitly, so a red preflight is never stepped over by
    # accident.
    ignore_preflight: bool = False


async def _account_value_for_heat(broker) -> float:
    """Account value for the heat denominator. 0.0 when unavailable, which
    makes heat None rather than a number computed off a guessed capital base."""
    try:
        acct = await broker.get_account_summary()
        return float(getattr(acct, "net_liquidation", 0) or 0)
    except Exception:
        return 0.0


@router.post("/dry-run")
async def rotation_dry_run(body: DryRunRequest, conn: HTTPConnection):
    """Run the full rotation workflow without submitting anything.

    Detect → Compare → Rotation Review → Approval → Risk Validation →
    Execution Intent. Every stage runs the real code; the final stage
    describes the exact orders that *would* be submitted instead of
    submitting them.

    The close order's side and quantity are derived the same way
    close_equity_trade derives them — from the broker's own live position,
    never from the DB's recorded quantity, which is the drift that produced
    real oversold positions in August.
    """
    from app.broker.broker_factory import get_broker
    from app.services.rotation_preflight import run_preflight

    broker = get_broker()
    stages: list[dict] = []

    # ── Gate: preflight ──────────────────────────────────────────────────
    preflight = await run_preflight(broker)
    stages.append({
        "stage": "preflight",
        "ok": preflight["all_clear"],
        "passed": f"{preflight.get('passed')}/{preflight.get('total')}",
        "failed": preflight.get("failed"),
        "unknown": preflight.get("unknown"),
    })
    if not preflight["all_clear"] and not body.ignore_preflight:
        return {
            "status": "blocked_by_preflight",
            "submitted_anything": False,
            "reason": (
                "Preflight is not clear. Rotation must not be rehearsed as if "
                "it were, because a clean dry-run against a dirty order book "
                "is exactly the false assurance this checklist exists to "
                "prevent. Re-run with ignore_preflight=true to see the "
                "comparison anyway — it still submits nothing."
            ),
            "stages": stages,
            "preflight": preflight,
        }

    # ── Detect ───────────────────────────────────────────────────────────
    from app.services.position_rotation import propose_rotation_incumbent
    ticker = (body.ticker or "DRYRUN").upper()
    incumbent = await propose_rotation_incumbent(incoming_ticker=ticker, broker=broker)
    stages.append({
        "stage": "detect",
        "ok": incumbent is not None,
        "incumbent": incumbent.underlying if incumbent else None,
        "detail": ("no eligible incumbent — Winner Protection excluded every "
                   "open position, or their P&L is unknown") if incumbent is None else None,
    })

    # ── Compare ──────────────────────────────────────────────────────────
    from app.core.config import settings
    from app.api.routes.trade_desk import _portfolio_heat_fraction
    from app.services.rotation_review import (
        MIN_CHALLENGER_VOLUME_RATIO, PositionFacts, build_rotation_review,
    )

    stop_dist = (abs(body.entry_price - body.stop_price)
                 if body.entry_price is not None and body.stop_price is not None else None)
    target_dist = (abs(body.target_price - body.entry_price)
                   if body.entry_price is not None and body.target_price is not None else None)

    review = build_rotation_review(
        incumbent=(
            PositionFacts(
                ticker=incumbent.underlying, side="incumbent",
                direction=incumbent.spread_type,
                quality_score=incumbent.quality_score,
                confidence=incumbent.confidence,
                in_flagged_cluster=incumbent.in_flagged_cluster,
                unrealized_pnl_context_only=incumbent.unrealized_pnl,
            ) if incumbent else PositionFacts(ticker="(none eligible)", side="incumbent")
        ),
        challenger=PositionFacts(
            ticker=ticker, side="challenger", direction=body.action,
            alpha_edge=body.alpha_edge_score, confidence=body.confidence,
            stop_distance=stop_dist, target_distance=target_dist,
            # Sourced the same way Stage 2b sources it, so the rehearsal
            # exercises the real constraint rather than a stub.
            liquidity_ok=(
                None if body.volume_ratio is None
                else body.volume_ratio >= MIN_CHALLENGER_VOLUME_RATIO
            ),
        ),
        portfolio_heat_fraction=await _portfolio_heat_fraction(
            await _account_value_for_heat(broker)),
        materiality_margin=float(getattr(
            settings, "rotation_review_materiality_margin", 15.0)),
    )
    stages.append({
        "stage": "compare",
        "ok": review["recommendation"] == "replace",
        "recommendation": review["recommendation"],
        "reasons": review["reasons"],
    })

    # ── Rotation Review ──────────────────────────────────────────────────
    stages.append({
        "stage": "rotation_review",
        "ok": True,
        "requires_approval": review["requires_approval"],
        "auto_executable": review["auto_executable"],
        "sunk_cost_excluded": review["sunk_cost_excluded"],
    })

    # ── Approval (simulated) ─────────────────────────────────────────────
    # No token is minted. This stage reports what the real approve route
    # would check, and confirms the guard is what stands between a review and
    # an order.
    stages.append({
        "stage": "approval",
        "ok": False,
        "detail": ("DRY RUN — no approval token minted, so no close is "
                   "permitted. In the live path a token exists only after the "
                   "atomic pending→approved transition on the review row."),
        "would_require": ["operator API key", "review still pending",
                          "kill switch clear", "incumbent still open"],
    })

    # ── Risk Validation ──────────────────────────────────────────────────
    from app.api.routes.trade_desk import _is_kill_switch_active
    incumbent_still_open = None
    if incumbent is not None:
        from sqlalchemy import select
        from app.api.scope_deps import scope_or_401
        from app.core.database import AsyncSessionLocal
        from app.models.trade import Trade
        from app.services.trade_scope import owned_by
        async with AsyncSessionLocal() as session:
            org_id = await scope_or_401(session, conn)
            row = (await session.execute(
                select(Trade).where(owned_by(org_id),
                                    Trade.id == incumbent.trade_id,
                                    Trade.status == "open")
            )).scalars().first()
        # A foreign incumbent reads as "not open", so the dry run reports the
        # rotation as not actionable rather than confirming the trade exists.
        incumbent_still_open = row is not None

    ks = _is_kill_switch_active()
    stages.append({
        "stage": "risk_validation",
        "ok": (not ks) and bool(incumbent_still_open),
        "kill_switch_engaged": ks,
        "incumbent_still_open": incumbent_still_open,
    })

    # ── Execution Intent ─────────────────────────────────────────────────
    # Exactly what would be sent. Side and quantity come from the broker's
    # live position, mirroring close_equity_trade.
    close_intent: Optional[dict] = None
    if incumbent is not None:
        try:
            live = await broker.get_equity_positions()
            match = next((p for p in live
                          if (getattr(p, "symbol", "") or "").upper()
                          == incumbent.underlying.upper()), None)
            if match is None:
                close_intent = {"error": "no live broker position — close would raise"}
            else:
                qty = abs(float(getattr(match, "quantity", 0) or 0))
                side = "SELL" if float(getattr(match, "quantity", 0) or 0) > 0 else "BUY"
                close_intent = {
                    "symbol": incumbent.underlying, "side": side, "quantity": qty,
                    "order_type": "MKT",
                    "source": "broker live position (not the DB quantity)",
                    "would_also": f"cancel all resting orders for {incumbent.underlying} first",
                }
        except Exception as exc:
            close_intent = {"error": f"{type(exc).__name__}: {exc}"}

    entry_intent = {
        "symbol": ticker, "side": body.action, "shares": body.shares,
        "entry_price": body.entry_price, "stop_price": body.stop_price,
        "target_price": body.target_price,
        "order_type": "bracket (parent + GTC stop + take-profit)",
        "note": ("would run the full _execute_signal gate stack — kill switch, "
                 "guardrails, frequency, portfolio gate, duplicate, cooldown — "
                 "any of which could still block it"),
    }
    stages.append({
        "stage": "execution_intent",
        "ok": True,
        "close_incumbent": close_intent,
        "enter_challenger": entry_intent,
    })

    return {
        "status": "dry_run_complete",
        "submitted_anything": False,
        "dry_run_at": datetime.now(timezone.utc).isoformat(),
        "preflight_clear": preflight["all_clear"],
        "stages": stages,
        "review": review,
    }
