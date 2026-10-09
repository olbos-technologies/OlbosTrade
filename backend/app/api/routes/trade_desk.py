"""
Trade Desk routes — execution mode + approval queue for Copilot/Autopilot.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.requests import HTTPConnection

from decimal import Decimal

from app.api.auth_deps import current_user
from app.api.deps import require_api_key
from app.api.tier_deps import require_broker_access
from app.api.rate_limit import rate_limit
from app.broker.ibkr_coordinator import Priority, ibkr_coordinator
from app.services.execution_mode import ExecutionMode, execution_mode_manager
from app.services.guardrails import GuardrailEngine, PortfolioState
from app.services.kill_switch import kill_switch_service
from app.services.observability import observability

logger = logging.getLogger(__name__)
router = APIRouter()

# Marketable buffer applied to the live bid/ask when repricing an equity
# entry's limit price just before submission — see _execute_signal's equity
# branch for why this exists (scan-time entry_price goes stale before the
# order reaches the broker).
_EQUITY_LIMIT_BUFFER = 0.001  # 10 bps


class RiskGateError(Exception):
    """Raised when the risk gate cannot safely evaluate — always fail closed."""


async def _fetch_portfolio_state() -> PortfolioState:
    """
    Load portfolio state from DB for guardrail evaluation.
    FAIL CLOSED: raises RiskGateError on any DB failure — never returns zero-defaults.
    Correct columns: Trade.pnl / Trade.exit_date / Trade.status (not realized_pnl/closed_at).
    """
    from app.core.config import settings as _cfg
    from app.core.database import AsyncSessionLocal
    from app.models.trade import Trade
    from app.services.account_state import get_account_value
    from sqlalchemy import select, func

    current_value = await get_account_value()

    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    try:
        async with AsyncSessionLocal() as _db:
            async def _sum_pnl(from_date: date) -> Decimal:
                row = await _db.execute(
                    select(func.sum(Trade.pnl)).where(
                        Trade.status == "closed",
                        Trade.exit_date >= from_date,
                        Trade.pnl.isnot(None),
                    )
                )
                val = row.scalar()
                return Decimal(str(val)) if val is not None else Decimal("0")

            daily_pnl   = await _sum_pnl(today)
            weekly_pnl  = await _sum_pnl(week_start)
            monthly_pnl = await _sum_pnl(month_start)

            # Daily trade cap counts every trade ENTERED today, regardless of
            # status — a trade opened and closed the same day still used a slot.
            trades_today = int((await _db.execute(
                select(func.count()).where(
                    func.date(Trade.entry_date) == today,
                )
            )).scalar() or 0)

            recent_pnl = (await _db.execute(
                select(Trade.pnl).where(Trade.status == "closed")
                .order_by(Trade.exit_date.desc()).limit(10)
            )).scalars().all()
            consecutive_losses = 0
            for pnl in recent_pnl:
                if pnl is not None and float(pnl) < 0:
                    consecutive_losses += 1
                else:
                    break

    except Exception as exc:
        raise RiskGateError(
            f"Guardrail DB read failed — refusing trade (fail closed): {exc}"
        ) from exc

    return PortfolioState(
        current_value=current_value,
        starting_capital=_cfg.starting_capital,
        daily_pnl=daily_pnl,
        weekly_pnl=weekly_pnl,
        monthly_pnl=monthly_pnl,
        consecutive_losses=consecutive_losses,
        trades_today=trades_today,
    )


# ── Kill switch ────────────────────────────────────────────────────────────────
# Single source of truth: kill_switch_service (from services/kill_switch.py).
# _kill_switch is a fast thread-safe mirror so the hot path doesn't need an
# async call — it is synced from kill_switch_service on every engage/reset.
_kill_switch = threading.Event()


def _is_kill_switch_active() -> bool:
    """Check both the local event and the authoritative service."""
    return _kill_switch.is_set() or kill_switch_service.is_engaged


async def _strategy_health_for(strategy: str):
    """
    Live health grade for one strategy from its closed trades, or None on error
    (the caller fails open). Kept thin and DB-scoped so the execution gate stays
    cheap.
    """
    try:
        from app.core.config import settings
        from app.core.database import AsyncSessionLocal
        from app.models.trade import Trade
        from app.services.strategy_health import evaluate_strategy_health
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            trades = (await session.execute(
                select(Trade).where(
                    Trade.strategy == strategy,
                    Trade.status == "closed",
                    Trade.pnl.isnot(None),
                )
            )).scalars().all()
        rows = [{"pnl": float(t.pnl), "entry_date": t.entry_date, "exit_date": t.exit_date}
                for t in trades]
        return evaluate_strategy_health(strategy, rows, settings.starting_capital)
    except Exception as exc:   # pragma: no cover - defensive; gate fails open
        logger.warning("Strategy health check failed for %s: %s", strategy, exc)
        return None

# ── Persisted pending approvals + execution log (Copilot mode / audit trail) ──
# Backed by the execution_events table so a deploy mid-Copilot-session doesn't
# silently drop pending approvals, and the execution log — the only audit trail
# of what autopilot actually did — survives restarts. Mirrors the kill switch's
# own DB-persistence pattern (GuardrailEvent, kill_switch.py).

async def _queue_pending_approval(signal: dict) -> None:
    """Queue a signal for Copilot/manual approval."""
    from app.core.database import AsyncSessionLocal
    from app.models.execution_event import ExecutionEvent

    signal_id = signal.get("id") or str(uuid.uuid4())
    signal["id"] = signal_id
    signal["queued_at"] = datetime.now(timezone.utc).isoformat()
    signal["status"] = "pending_approval"

    async with AsyncSessionLocal() as session:
        async with session.begin():
            session.add(ExecutionEvent(
                kind="pending_approval",
                signal_id=signal_id,
                ticker=signal.get("ticker"),
                asset_type=signal.get("asset_type"),
                status="pending",
                payload=signal,
            ))


def _challenger_liquidity_ok(signal: dict) -> Optional[bool]:
    """Is the challenger liquid enough to enter? None when unknown.

    volume_ratio lives at signal["indicators"]["volume_ratio"], not at the top
    level — an earlier version of this read signal.get("volume_ratio") and so
    was always None, which made every review fail the liquidity constraint and
    return "hold". The review machinery was correct; the input was simply not
    connected.

    None is still returned when the indicator is genuinely absent (an options
    signal, or a scan that produced no volume data), and the hard-constraint
    check treats that as a veto rather than a pass.
    """
    from app.services.rotation_review import MIN_CHALLENGER_VOLUME_RATIO
    ind = signal.get("indicators") or {}
    vr = ind.get("volume_ratio")
    if vr is None:
        return None
    try:
        return float(vr) >= MIN_CHALLENGER_VOLUME_RATIO
    except (TypeError, ValueError):
        return None


async def _portfolio_heat_fraction(account_value: float) -> Optional[float]:
    """True risk-at-stake as a FRACTION of capital, or None if unavailable.

    Deliberately NOT portfolio_engine.compute_portfolio_risk(). That function
    defines equity "risk_dollars" as entry x shares — full notional, its own
    comment calls it a worst-case proxy — so its portfolio_heat_pct measures
    how invested the book is, not how much is at risk. On 2026-08-29 it read
    94.06% against $220,715 of notional on $234,651 of capital, which is
    accurate as deployment and meaningless as risk. Gating rotation on it with
    a 35% ceiling made the constraint unsatisfiable.

    Here risk is the real thing: |entry - stop| x shares, with the stop read
    from the position's live protective order at the broker. A position whose
    stop cannot be found contributes None, and any None makes the whole
    measurement None — a partial sum would understate heat, and understating
    the denominator of a safety check is the wrong direction to be wrong in.

    The Risk Monitor still displays the notional-based number; correcting that
    is a wider change touching eight call sites and the UI's own thresholds.
    """
    try:
        from sqlalchemy import select
        from app.broker.broker_factory import get_broker
        from app.core.database import AsyncSessionLocal
        from app.models.trade import Trade

        if not account_value or account_value <= 0:
            return None

        async with AsyncSessionLocal() as session:
            open_trades = (await session.execute(
                select(Trade).where(Trade.status == "open")
            )).scalars().all()
        if not open_trades:
            return 0.0

        book = await get_broker().get_open_orders(refresh=True)
        if book.get("source") != "refreshed":
            # A cache fall-back cannot prove a stop is absent vs unseen.
            return None
        stops: dict[str, float] = {}
        for o in book.get("orders", []):
            if not o.get("is_protective") or (o.get("remaining") or 0) <= 0:
                continue
            px = o.get("stop_price")
            sym = (o.get("symbol") or "").upper()
            if sym and px:
                # Widest stop per symbol = worst case across tranches.
                stops[sym] = max(stops.get(sym, 0.0), float(px))

        total = 0.0
        for t in open_trades:
            sym = (t.underlying or "").upper()
            entry = float(getattr(t, "credit_received", 0) or 0)
            qty = abs(int(getattr(t, "quantity", 0) or 0))
            stop = stops.get(sym)
            if stop is None or entry <= 0 or qty <= 0:
                logger.info(
                    "rotation heat: no protective stop for %s — heat unmeasurable", sym)
                return None
            total += abs(entry - stop) * qty

        return round(total / account_value, 4)
    except Exception as exc:
        logger.warning("rotation review: portfolio heat unavailable: %s", exc)
        return None


async def _queue_rotation_review(entry: dict) -> str:
    """Persist a ROTATION_REVIEW awaiting approval. Same table and same
    pending→resolved lifecycle as _queue_pending_approval, under a distinct
    kind so the two queues stay separately addressable."""
    from app.core.database import AsyncSessionLocal
    from app.models.execution_event import ExecutionEvent

    review_id = str(uuid.uuid4())
    entry["review_id"] = review_id
    entry["queued_at"] = datetime.now(timezone.utc).isoformat()

    async with AsyncSessionLocal() as session:
        async with session.begin():
            session.add(ExecutionEvent(
                kind="rotation_review",
                signal_id=review_id,
                ticker=entry.get("ticker"),
                asset_type=entry.get("asset_type"),
                status="pending",
                payload=entry,
            ))
    return review_id


async def _get_pending_approvals() -> list[dict]:
    from app.core.database import AsyncSessionLocal
    from app.models.execution_event import ExecutionEvent
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(ExecutionEvent)
            .where(ExecutionEvent.kind == "pending_approval", ExecutionEvent.status == "pending")
            .order_by(ExecutionEvent.created_at.desc())
        )).scalars().all()
    return [r.payload for r in rows]


def _approval_actor(conn: HTTPConnection) -> Optional[str]:
    """Identify the signed-in caller who is approving or rejecting, if any.

    Returns None rather than a placeholder when auth is disabled, because an
    audit trail that says "user" for every decision is worse than one that
    admits it does not know: the first looks like evidence and is not.
    """
    user = current_user(conn)
    identity = user.get("id") or user.get("email")
    return str(identity) if identity else None


async def _resolve_pending_approval(
    signal_id: str,
    resolution: str,
    actor: Optional[str] = None,
) -> Optional[dict]:
    """Atomically claim a pending approval and return its payload.

    Returns None when there was no PENDING row to claim — it never existed, or
    another request resolved it first. Callers turn that into a 404, which is
    what makes an approval single-use.

    THE CLAIM IS ONE CONDITIONAL UPDATE, NOT A SELECT FOLLOWED BY A WRITE.
    That distinction is the whole point of this function. Under READ COMMITTED
    a bare SELECT takes no row lock, so two concurrent approvals of the same
    signal both read status='pending', both assign, and both commit: two
    callers each receive a payload, and each goes on to submit an order for
    one signal. `UPDATE ... WHERE status='pending'` cannot do that. Postgres
    waits on the row lock and then RE-EVALUATES the WHERE clause against the
    committed version, so the loser matches zero rows and gets None. The
    rotation path next door has always done this via SELECT ... FOR UPDATE;
    this path did not, and that asymmetry was the bug.

    `actor` is recorded with the decision so approval history says who
    authorised it. It is deliberately best-effort: these routes are API-key
    gated and `current_user` is {} when auth_enabled is off, in which case
    there is no authenticated actor to name and the field is omitted rather
    than filled with a guess.
    """
    from app.core.database import AsyncSessionLocal
    from app.models.execution_event import ExecutionEvent
    from sqlalchemy import cast, update
    from sqlalchemy.dialects.postgresql import JSONB

    decision = {
        "resolution": resolution,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }
    if actor:
        decision["resolved_by"] = actor

    async with AsyncSessionLocal() as session:
        async with session.begin():
            claimed = (await session.execute(
                update(ExecutionEvent)
                .where(
                    ExecutionEvent.kind == "pending_approval",
                    ExecutionEvent.signal_id == signal_id,
                    ExecutionEvent.status == "pending",
                )
                .values(
                    status=resolution,
                    # Merge, not replace: the decision is appended to whatever
                    # the signal already carries so history is not overwritten.
                    payload=ExecutionEvent.payload.op("||")(
                        cast({"approval": decision}, JSONB)
                    ),
                )
                .returning(ExecutionEvent.payload)
                .execution_options(synchronize_session=False)
            )).first()
    if claimed is None:
        return None
    return dict(claimed[0] or {})


async def _log_execution(entry: dict) -> None:
    from app.core.database import AsyncSessionLocal
    from app.models.execution_event import ExecutionEvent

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                session.add(ExecutionEvent(
                    kind="execution",
                    signal_id=entry.get("signal_id") or entry.get("id"),
                    ticker=entry.get("ticker"),
                    asset_type=entry.get("asset_type"),
                    status=entry.get("result"),
                    payload=entry,
                ))
    except Exception as exc:
        logger.critical(
            "CRITICAL: execution result for %s could not be persisted to the "
            "execution log: %s — result was: %s",
            entry.get("ticker"), exc, entry,
        )


class SetExecutionModeRequest(BaseModel):
    mode: str   # manual | copilot | autopilot


class EquityEvaluateRequest(BaseModel):
    """Advisory evaluation for Equity Desk composer — does not place orders."""
    ticker: str
    action: str = "BUY"
    shares: int = 1
    entry_price: float
    stop_price: float
    target_price: Optional[float] = None
    order_type: str = "limit"


class KillSwitchRequest(BaseModel):
    engaged: bool
    # Required when engaged=False — must match KILL_SWITCH_RESET_CODE on the server.
    authorization_code: Optional[str] = None


class ManualTradeRequest(BaseModel):
    ticker:      str
    action:      str            # BUY | SELL
    shares:      int   = 1
    order_type:  str   = "market"   # market | limit
    limit_price: Optional[float] = None


# ── Kill switch endpoints ──────────────────────────────────────────────────────

@router.get("/kill-switch")
async def get_kill_switch():
    """Return current kill switch state (checks both local event and service)."""
    return {"engaged": _is_kill_switch_active()}


def _sanitize_kill_switch_errors(errors: list) -> list[str]:
    """Reduce engage()'s raw error strings to a safe, still-useful label.

    engage() formats these as "<stage>_<symbol>: <str(exc)>" — the stage and
    symbol are operationally necessary (which position did not flatten), the
    exception text is not, and on an unauthenticated route it is a disclosure.
    Keep everything up to the first colon; drop the rest.
    """
    out: list[str] = []
    for e in errors or []:
        label = str(e).split(":", 1)[0].strip()
        out.append(label or "unknown_error")
    return out

@router.post("/kill-switch")
async def set_kill_switch(body: KillSwitchRequest):
    """
    Engage or reset the kill switch — syncs both the service and the local event.

    Engage is intentionally unauthenticated at the FastAPI layer (emergency stop;
    nginx Basic Auth still applies in production). Reset requires a server-side
    authorization code and never accepts a hardcoded client secret.
    """
    if body.engaged:
        _kill_switch.set()
        result = await kill_switch_service.engage("manual via trade-desk API")
        logger.warning("KILL SWITCH ENGAGED via API — all order submission halted")
        # Return what engage() actually DID, not just that it ran. Engaging is
        # the only bulk-flatten path this app has, and the report is the only
        # evidence the flatten happened: engage() attempts every step even when
        # an earlier one fails, so it can pause the scheduler, fail to reach
        # the broker, and still come back a "success". Worse, a second engage
        # on an already-engaged switch returns at the top having flattened
        # NOTHING. Collapsing all of that to {"engaged": true} told an operator
        # watching positions stay open that the close-all had worked.
        return {
            "engaged": _is_kill_switch_active(),
            "already_engaged": result.get("status") == "already_engaged",
            "positions_flattened": result.get("positions_flattened", 0),
            # positions_flattened counts every non-rejected order, including
            # `submitted` (no fill yet), `partial` (residual exposure) and
            # `cancelled`. Only `filled` means the position is actually gone,
            # so the per-status tally travels with it — reporting the count
            # alone would tell an operator the book is flat when it is not.
            "flatten_statuses": result.get("flatten_statuses", {}),
            "orders_cancelled": result.get("orders_cancelled", 0),
            # Error LABELS, not raw exception text. This endpoint has no
            # require_api_key by design (emergency stop), and the frontend can
            # serve without Basic Auth when DASH_USER/DASH_PASS are blank — so
            # an unauthenticated caller reaches this response. engage() builds
            # its errors from str(exc), which carries broker internals,
            # database DSNs and stack detail. The operator needs to know THAT
            # something failed and roughly where; the full text belongs in the
            # server log, which is already written by engage().
            "errors": _sanitize_kill_switch_errors(result.get("errors", [])),
        }
    else:
        result = await kill_switch_service.reset(body.authorization_code or "")
        if not result.get("reset"):
            raise HTTPException(status_code=403, detail=result)
        _kill_switch.clear()
        logger.info("Kill switch reset via API — order submission resumed")
    return {"engaged": _is_kill_switch_active()}


# ── Execution mode ─────────────────────────────────────────────────────────────

@router.get("/execution-mode")
async def get_execution_mode():
    return execution_mode_manager.summary()


@router.post("/execution-mode", dependencies=[Depends(require_api_key), Depends(rate_limit), Depends(require_broker_access)])
async def set_execution_mode(body: SetExecutionModeRequest):
    try:
        mode = ExecutionMode(body.mode)
    except ValueError:
        raise HTTPException(400, f"Invalid mode. Valid: manual, copilot, autopilot")
    result = await execution_mode_manager.set_mode(mode)
    if result.get("persistence") == "unavailable":
        # The escalation was REFUSED, not applied. Returning 200 with the old
        # mode in the body is how this used to read as success to a client
        # that only checks the status code.
        raise HTTPException(
            503,
            detail={
                "error": "execution_mode_not_recorded",
                "message": result.get("detail", "Mode change could not be recorded."),
                **{k: result[k] for k in ("mode", "requested_mode") if k in result},
            },
        )
    return result


# ── Pending approvals (Copilot) ────────────────────────────────────────────────

@router.get("/pending")
async def get_pending():
    """List signals awaiting user approval in Copilot mode."""
    items = sorted(await _get_pending_approvals(),
                   key=lambda x: x.get("queued_at", ""), reverse=True)
    return {
        "mode":    execution_mode_manager.mode.value,
        "pending": items,
        "count":   len(items),
    }


@router.post("/evaluate-equity")
async def evaluate_equity(req: EquityEvaluateRequest):
    """
    Backend-authoritative eligibility preview for the Equity Desk composer.

    Does NOT call _execute_signal and does NOT place orders. Aggregates kill
    switch, market hours, regime, account mode, and guardrail state into a
    TradeIntent-style final_status for UI display.
    """
    from datetime import datetime, timezone
    from app.core.config import settings
    from app.services.account_guard import verify_account_mode

    block_reasons: list[str] = []
    warnings: list[str] = []

    ticker = (req.ticker or "").upper().strip()
    if not ticker:
        block_reasons.append("Ticker is required")
    if req.shares < 1:
        block_reasons.append("Shares must be at least 1")
    if req.entry_price <= 0 or req.stop_price <= 0:
        block_reasons.append("Entry and stop prices must be positive")
    if req.action.upper() not in ("BUY", "SELL"):
        block_reasons.append("Action must be BUY or SELL")

    if req.action.upper() == "BUY" and req.stop_price >= req.entry_price:
        warnings.append("Stop is not below entry for a BUY")
    if req.action.upper() == "SELL" and req.stop_price <= req.entry_price:
        warnings.append("Stop is not above entry for a SELL")

    env = "paper" if settings.is_paper_trading else "live"

    if _is_kill_switch_active():
        block_reasons.append("Kill switch is engaged")

    if getattr(settings, "market_hours_only", True):
        from app.utils.market_hours import is_market_open, market_status
        if not is_market_open():
            st = market_status()
            block_reasons.append(
                f"Market closed ({st.get('session', 'outside RTH')})"
            )

    try:
        from app.main import _current_regime
        if _current_regime is not None and not getattr(
            _current_regime, "equity_allowed", True
        ):
            block_reasons.append(
                f"Regime does not allow equities "
                f"({getattr(getattr(_current_regime, 'regime', None), 'value', 'unknown')})"
            )
    except Exception:
        warnings.append("Regime state unavailable — treat with caution")

    try:
        from app.broker.broker_factory import get_broker
        broker = get_broker()
        _ok, _detail = await verify_account_mode(broker)
        if not _ok:
            block_reasons.append(f"Account mode: {_detail}")
    except Exception as exc:
        warnings.append(f"Account mode check skipped: {exc}")

    try:
        portfolio_state = await _fetch_portfolio_state()
        guardrail = GuardrailEngine().check_all(portfolio_state)
        if not guardrail.trading_allowed:
            block_reasons.append(
                guardrail.reason or f"Guardrails blocked ({guardrail.trading_mode})"
            )
        elif guardrail.trading_mode == "capital_preservation":
            warnings.append("Capital preservation mode — size may be reduced at execution")
        try:
            from app.services.execution_portfolio_gate import check_execution_portfolio
            pg = await check_execution_portfolio(
                {
                    "ticker": ticker,
                    "asset_type": "equity",
                    "trade_plan": {
                        "shares": req.shares,
                        "entry_price": req.entry_price,
                        "stop_price": req.stop_price,
                    },
                },
                portfolio_value=float(portfolio_state.current_value or 0),
            )
            if not pg.allowed:
                block_reasons.append(pg.reason or "Portfolio gate blocked")
        except Exception as pg_exc:
            warnings.append(f"Portfolio gate preview incomplete: {pg_exc}")
    except RiskGateError as exc:
        block_reasons.append(str(exc))
    except Exception as exc:
        warnings.append(f"Guardrail check incomplete: {exc}")

    mode = execution_mode_manager.mode.value
    if block_reasons:
        final_status = "BLOCKED"
    elif mode == "copilot":
        final_status = "COPILOT_REVIEW_REQUIRED"
    elif mode == "manual":
        final_status = "MANUAL_ELIGIBLE"
    elif mode == "autopilot":
        # Scan/composer path still queues — be honest
        final_status = "COPILOT_REVIEW_REQUIRED"
        warnings.append(
            "Autopilot scan/composer auto-execute is disabled; submit queues for approval"
        )
    else:
        final_status = "INFORMATIONAL"

    risk_dollars = abs(req.entry_price - req.stop_price) * req.shares
    notional = req.entry_price * req.shares

    return {
        "final_status": final_status,
        "block_reasons": block_reasons,
        "warnings": warnings,
        "environment": env,
        "execution_mode": mode,
        "ticker": ticker,
        "action": req.action.upper(),
        "shares": req.shares,
        "estimated_notional": round(notional, 2),
        "estimated_max_loss": round(risk_dollars, 2),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_version": "equity-desk-c1",
    }


class OptionsEvaluateRequest(BaseModel):
    """Advisory evaluation for Options Desk — does not place orders."""
    ticker: str
    strategy: str = "bull_put_spread"
    dte: float = 30


@router.post("/evaluate-options")
async def evaluate_options(req: OptionsEvaluateRequest):
    """
    Backend-authoritative eligibility preview for the Options Desk.

    Does NOT call _execute_signal. Blocks naked / iron_condor / 0DTE autopilot
    strategies explicitly. Defined-risk and income strategies may be MANUAL or
    COPILOT eligible subject to kill switch, hours, regime, and guardrails.
    """
    from datetime import datetime, timezone
    from app.core.config import settings
    from app.services.account_guard import verify_account_mode

    block_reasons: list[str] = []
    warnings: list[str] = []

    ticker = (req.ticker or "").upper().strip()
    strategy = (req.strategy or "").lower().strip()
    allowed = {
        "bull_put_spread", "bear_call_spread", "bull_call_spread", "bear_put_spread",
        "cash_secured_put", "covered_call",
    }
    banned = {"naked_call", "naked_put", "iron_condor", "short_straddle", "short_strangle"}

    if not ticker:
        block_reasons.append("Ticker is required")
    if strategy in banned:
        block_reasons.append(f"Strategy '{strategy}' is not permitted")
    elif strategy and strategy not in allowed:
        warnings.append(f"Strategy '{strategy}' is not in the Phase D supported set")

    if req.dte is not None and req.dte <= 1:
        warnings.append("0DTE / 1DTE: Autopilot disabled — Copilot or manual only")
        # Do not block evaluate for read-only desk; warn strongly
        warnings.append("0DTE Autopilot remains disabled by policy")

    env = "paper" if settings.is_paper_trading else "live"

    if _is_kill_switch_active():
        block_reasons.append("Kill switch is engaged")

    if getattr(settings, "market_hours_only", True):
        from app.utils.market_hours import is_market_open, market_status
        if not is_market_open():
            st = market_status()
            block_reasons.append(
                f"Market closed ({st.get('session', 'outside RTH')})"
            )

    try:
        from app.main import _current_regime
        if _current_regime is not None and not getattr(
            _current_regime, "options_allowed", True
        ):
            block_reasons.append(
                f"Regime does not allow options "
                f"({getattr(getattr(_current_regime, 'regime', None), 'value', 'unknown')})"
            )
    except Exception:
        warnings.append("Regime state unavailable — treat with caution")

    try:
        from app.broker.broker_factory import get_broker
        broker = get_broker()
        _ok, _detail = await verify_account_mode(broker)
        if not _ok:
            block_reasons.append(f"Account mode: {_detail}")
    except Exception as exc:
        warnings.append(f"Account mode check skipped: {exc}")

    try:
        portfolio_state = await _fetch_portfolio_state()
        guardrail = GuardrailEngine().check_all(portfolio_state)
        if not guardrail.trading_allowed:
            block_reasons.append(
                guardrail.reason or f"Guardrails blocked ({guardrail.trading_mode})"
            )
        elif guardrail.trading_mode == "capital_preservation":
            warnings.append("Capital preservation mode — size may be reduced at execution")
        try:
            from app.services.execution_portfolio_gate import check_execution_portfolio
            # Preview uses a nominal 1-lot defined-risk placeholder when no spread
            # payload is supplied by the desk composer.
            pg = await check_execution_portfolio(
                {
                    "ticker": ticker,
                    "asset_type": "options",
                    "strategy": strategy or "bull_put_spread",
                    "quantity": 1,
                    "spread": {"max_loss": 250.0},
                },
                portfolio_value=float(portfolio_state.current_value or 0),
            )
            if not pg.allowed:
                block_reasons.append(pg.reason or "Portfolio gate blocked")
        except Exception as pg_exc:
            warnings.append(f"Portfolio gate preview incomplete: {pg_exc}")
    except RiskGateError as exc:
        block_reasons.append(str(exc))
    except Exception as exc:
        warnings.append(f"Guardrail check incomplete: {exc}")

    mode = execution_mode_manager.mode.value
    if block_reasons:
        final_status = "BLOCKED"
    elif mode == "copilot":
        final_status = "COPILOT_REVIEW_REQUIRED"
    elif mode == "manual":
        final_status = "MANUAL_ELIGIBLE"
    elif mode == "autopilot":
        final_status = "COPILOT_REVIEW_REQUIRED"
        warnings.append(
            "Options Autopilot from desk tools queues for approval; 0DTE Autopilot stays off"
        )
    else:
        final_status = "INFORMATIONAL"

    return {
        "final_status": final_status,
        "block_reasons": block_reasons,
        "warnings": warnings,
        "environment": env,
        "execution_mode": mode,
        "ticker": ticker,
        "strategy": strategy,
        "dte": req.dte,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "evaluation_version": "options-desk-d1",
        "policy": {
            "naked_shorts": False,
            "iron_condor_execute": False,
            "zero_dte_autopilot": False,
        },
    }


@router.post("/approve/{signal_id}", dependencies=[Depends(require_api_key), Depends(rate_limit), Depends(require_broker_access)])
async def approve_signal(signal_id: str, conn: HTTPConnection):
    """User approves a pending signal → executes order.

    The claim happens first and is single-use. If it returns None this request
    lost the race (or there was nothing to approve) and MUST NOT execute —
    that is the difference between one order and two.
    """
    actor = _approval_actor(conn)
    signal = await _resolve_pending_approval(signal_id, "approved", actor=actor)
    if signal is None:
        raise HTTPException(404, "Signal not found in pending queue")

    result = await _execute_signal(signal, approved_by="user")
    await _log_execution({
        **result,
        "signal_id": signal_id,
        "approved_by": "user",
        # Who, specifically — "user" is a role, not an identity.
        "approved_by_actor": actor,
    })
    return result


@router.get("/rotation-reviews")
async def get_rotation_reviews():
    """Pending ROTATION_REVIEW intents awaiting approval. Read-only."""
    from app.core.database import AsyncSessionLocal
    from app.models.execution_event import ExecutionEvent
    from sqlalchemy import select

    try:
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                select(ExecutionEvent)
                .where(ExecutionEvent.kind == "rotation_review",
                       ExecutionEvent.status == "pending")
                .order_by(ExecutionEvent.created_at.desc())
                .limit(50)
            )).scalars().all()
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}", "reviews": []}
    return {"status": "ok", "reviews": [r.payload for r in rows]}


async def _resolve_rotation_review(review_id: str, resolution: str) -> Optional[dict]:
    """Atomically flip a pending rotation review to approved/rejected.

    Returns None if it does not exist or was already resolved — which is what
    makes an approval single-use. Two concurrent approvals cannot both close
    the same incumbent, because only the first transition finds status
    'pending'.
    """
    from app.core.database import AsyncSessionLocal
    from app.models.execution_event import ExecutionEvent
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        async with session.begin():
            row = (await session.execute(
                select(ExecutionEvent).where(
                    ExecutionEvent.kind == "rotation_review",
                    ExecutionEvent.signal_id == review_id,
                    ExecutionEvent.status == "pending",
                ).with_for_update()
            )).scalars().first()
            if row is None:
                return None
            row.status = resolution
            return dict(row.payload or {})


@router.post("/rotation-review/{review_id}/approve",
             dependencies=[Depends(require_api_key), Depends(rate_limit), Depends(require_broker_access)])
async def approve_rotation_review(review_id: str):
    """Approve a replacement: close the incumbent, then enter the challenger.

    Everything here is deliberate rather than convenient:

    * The review is re-validated, not trusted. It may be minutes old; prices
      move, the kill switch may have been thrown, the incumbent may already
      be gone. Approving a stale document must not send a stale order.
    * The incumbent closes first. If that fails, the challenger is NOT
      entered — the slot was never freed, so entering would breach
      max_positions, which is the constraint that started all this.
    * If the close succeeds but the entry fails, that is reported plainly.
      The slot is free and unused, which is a safe state, not a silent one.
    """
    review = await _resolve_rotation_review(review_id, "approved")
    if review is None:
        raise HTTPException(404, "Rotation review not found, or already resolved")

    if _is_kill_switch_active():
        raise HTTPException(423, "Kill switch engaged — no orders may be sent")

    incumbent_trade_id = review.get("incumbent_trade_id")
    if not incumbent_trade_id:
        raise HTTPException(
            400, "Review has no incumbent to close (recommendation was not actionable)")

    from sqlalchemy import select
    from app.broker.broker_factory import get_broker
    from app.core.database import AsyncSessionLocal
    from app.models.trade import Trade
    from app.services.position_rotation import ROTATION_CLOSED_BY, close_equity_trade, close_options_trade

    async with AsyncSessionLocal() as session:
        trade = (await session.execute(
            select(Trade).where(Trade.id == incumbent_trade_id,
                                Trade.status == "open")
        )).scalars().first()
    if trade is None:
        raise HTTPException(
            409, "Incumbent is no longer open — it closed since this review was raised")

    broker = get_broker()
    spread_type = (trade.spread_type or "").lower()
    close_fn = close_options_trade if spread_type in ("put", "call") else close_equity_trade
    try:
        # review_id is the approval token: it exists only after the atomic
        # pending→approved transition above, so it cannot be replayed and
        # cannot be forged by a caller who never went through this route.
        close_receipt = await close_fn(
            trade, broker=broker, closed_by=ROTATION_CLOSED_BY,
            rotation_approval=review_id,
        )
    except Exception as exc:
        await _log_execution({"kind": "ROTATION_REVIEW", "review_id": review_id,
                              "result": "close_failed", "error": str(exc)})
        raise HTTPException(502, f"Incumbent close failed, challenger not entered: {exc}")

    challenger = review.get("challenger_signal") or {}
    entry_result = await _execute_signal(challenger, approved_by=f"rotation_review:{review_id}")

    out = {
        "kind": "ROTATION_REVIEW",
        "review_id": review_id,
        "result": "approved",
        "closed": close_receipt,
        "entered": entry_result,
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    await _log_execution(out)
    return out


@router.post("/rotation-review/{review_id}/reject",
             dependencies=[Depends(require_api_key), Depends(rate_limit), Depends(require_broker_access)])
async def reject_rotation_review(review_id: str):
    """Decline a replacement. Nothing is closed and nothing is entered."""
    review = await _resolve_rotation_review(review_id, "rejected")
    if review is None:
        raise HTTPException(404, "Rotation review not found, or already resolved")
    out = {
        "kind": "ROTATION_REVIEW", "review_id": review_id, "result": "rejected",
        "ticker": review.get("ticker"),
        "rejected_at": datetime.now(timezone.utc).isoformat(),
    }
    await _log_execution(out)
    return out


@router.post("/reject/{signal_id}", dependencies=[Depends(require_api_key), Depends(rate_limit), Depends(require_broker_access)])
async def reject_signal(signal_id: str, conn: HTTPConnection):
    """User rejects a pending signal — no order sent.

    Shares the single-use claim with approve, so a concurrent approve/reject
    pair produces exactly one terminal decision rather than both.
    """
    actor = _approval_actor(conn)
    signal = await _resolve_pending_approval(signal_id, "rejected", actor=actor)
    if signal is None:
        raise HTTPException(404, "Signal not found in pending queue")

    entry = {
        "signal_id":   signal_id,
        "ticker":      signal.get("ticker"),
        "asset_type":  signal.get("asset_type", "equity"),
        "action":      signal.get("action"),
        "result":      "rejected",
        "rejected_at": datetime.now(timezone.utc).isoformat(),
        "rejected_by": "user",
        "rejected_by_actor": actor,
    }
    await _log_execution(entry)
    return entry


@router.post("/reject-all", dependencies=[Depends(require_api_key), Depends(rate_limit)])
async def reject_all_pending():
    """
    Bulk-reject every currently-pending approval in one call.

    Intended for clearing a stale queue (e.g. signals that queued in COPILOT
    mode before the operator switched to AUTOPILOT, or a backlog accumulated
    while the operator was away).  Each rejected item is written to the
    execution log exactly as a per-signal rejection would be.
    """
    from app.core.database import AsyncSessionLocal
    from app.models.execution_event import ExecutionEvent
    from sqlalchemy import select

    now = datetime.now(timezone.utc).isoformat()

    async with AsyncSessionLocal() as session:
        async with session.begin():
            rows = (await session.execute(
                select(ExecutionEvent)
                .where(ExecutionEvent.kind == "pending_approval", ExecutionEvent.status == "pending")
            )).scalars().all()

            rejected: list[dict] = []
            for row in rows:
                row.status = "rejected"
                payload = row.payload or {}
                rejected.append({
                    "signal_id":   row.signal_id,
                    "ticker":      payload.get("ticker"),
                    "asset_type":  payload.get("asset_type", "equity"),
                    "action":      payload.get("action"),
                    "result":      "rejected",
                    "rejected_at": now,
                    "rejected_by": "user:bulk",
                })

    for entry in rejected:
        await _log_execution(entry)

    return {
        "rejected": len(rejected),
        "rejected_at": now,
    }


# ── Manual trade ──────────────────────────────────────────────────────────────

@router.post("/manual-trade", dependencies=[Depends(require_api_key), Depends(rate_limit), Depends(require_broker_access)])
async def manual_trade(req: ManualTradeRequest):
    """
    Force a manual equity order — bypasses signal scoring and IV filters
    but must still pass all risk guardrails (kill switch + loss limits + trade cap).
    """
    signal = {
        "id":         str(uuid.uuid4()),
        "ticker":     req.ticker.upper(),
        "action":     req.action.upper(),
        "asset_type": "equity",
        "trade_plan": {
            "shares":      req.shares,
            "entry_price": req.limit_price,
            "stop_price":  None,
            "target_price": None,
        },
        "manual":     True,
        "order_type": req.order_type,
    }
    result = await _execute_signal(signal, approved_by="manual")
    if result.get("result") == "error":
        raise HTTPException(500, result.get("error", "execution error"))
    await _log_execution(result)
    return result


class ClosePositionRequest(BaseModel):
    trade_id: str
    order_type: str = "market"          # market | limit
    limit_price: Optional[float] = None


@router.post("/close-position", dependencies=[Depends(require_api_key), Depends(rate_limit), Depends(require_broker_access)])
async def close_position(req: ClosePositionRequest):
    """
    Manually close an open position — the operator's own "I want out now"
    action, independent of the automated profit-target/stop-loss exit.

    Does NOT go through _execute_signal (that pipeline's duplicate guard
    blocks *any* new order on a ticker with an existing open trade — exactly
    the position this endpoint exists to close — so it would reject a
    closing order as "already_open"). Kill switch is deliberately NOT
    checked here: closing reduces risk, and kill-switch engagement already
    flattens positions on its own — blocking a manual close during a
    kill-switch event would be backwards.

    Equity closes support order_type/limit_price; 2-leg options spreads
    (put/call) close via a market-order mirrored combo — see
    close_options_trade()'s docstring for why LMT isn't supported there yet.
    """
    from app.core.database import AsyncSessionLocal
    from app.models.trade import Trade
    from sqlalchemy import select

    try:
        trade_uuid = uuid.UUID(req.trade_id)
    except ValueError:
        raise HTTPException(400, "Invalid trade_id")

    async with AsyncSessionLocal() as session:
        trade = (await session.execute(
            select(Trade).where(Trade.id == trade_uuid)
        )).scalar_one_or_none()

    if trade is None:
        raise HTTPException(404, "Trade not found")
    if trade.status != "open":
        raise HTTPException(409, f"Trade is not open (status={trade.status})")

    spread_type = (trade.spread_type or "").lower()
    if spread_type not in ("equity_long", "equity_short", "put", "call"):
        raise HTTPException(
            400,
            f"Cannot close trade with spread_type={spread_type!r} — expected "
            "an equity direction (equity_long/equity_short) or an options "
            "type (put/call).",
        )

    from app.broker.broker_factory import get_broker
    from app.services.position_rotation import close_equity_trade, close_options_trade

    broker = get_broker()
    try:
        if spread_type in ("put", "call"):
            # Options close is market-order only in this increment —
            # req.order_type/req.limit_price are intentionally not threaded
            # through; see close_options_trade()'s docstring.
            entry = await close_options_trade(trade, broker=broker, closed_by="manual")
        else:
            entry = await close_equity_trade(
                trade,
                broker=broker,
                closed_by="manual",
                order_type=req.order_type,
                limit_price=req.limit_price,
            )
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    await _log_execution(entry)
    return entry


class CloseUntrackedPositionRequest(BaseModel):
    symbol: str
    order_type: str = "market"
    limit_price: Optional[float] = None


@router.post("/close-untracked-position", dependencies=[Depends(require_api_key), Depends(rate_limit), Depends(require_broker_access)])
async def close_untracked_position(req: CloseUntrackedPositionRequest):
    """
    Close a live broker equity position that has no matching DB Trade row —
    e.g. a fill the app lost track of after an order-placement timeout (the
    coordinator's wait_for gives up on a shielded request that keeps running
    and can still fill after the caller already treated it as failed).

    Sourced entirely from the broker's own live position, not a DB row —
    that's the only trusted source of truth here. If a DB Trade IS open for
    this symbol, this is the wrong endpoint: use /close-position with its
    trade_id instead, so the DB row's exit gets recorded and doesn't desync.

    Equity only, same deliberate scope limit as /close-position — closing a
    real 2-leg options position needs mirrored-spread submission logic.
    """
    from app.core.database import AsyncSessionLocal
    from app.models.trade import Trade
    from sqlalchemy import select

    ticker = req.symbol.upper()

    async with AsyncSessionLocal() as session:
        existing = (await session.execute(
            select(Trade).where(Trade.underlying == ticker, Trade.status == "open")
        )).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            409,
            f"{ticker} has an open, tracked Trade ({existing.id}) — "
            "use POST /close-position with that trade_id instead.",
        )

    from app.broker.broker_factory import get_broker
    broker = get_broker()
    broker_positions = await broker.get_positions()
    pos = next((p for p in broker_positions if p.symbol == ticker or p.underlying == ticker), None)
    if pos is None or pos.quantity == 0:
        raise HTTPException(404, f"No open broker position found for {ticker}")
    if pos.asset_type != "equity":
        raise HTTPException(
            400,
            f"{ticker} is an options position — close it directly with the broker for now.",
        )

    close_side = "SELL" if pos.quantity > 0 else "BUY"
    qty = abs(int(pos.quantity))

    cancelled = await broker.cancel_open_orders(ticker)

    result = await ibkr_coordinator.submit(
        Priority.P0,
        lambda: broker.place_equity_order(
            ticker=ticker, qty=qty, side=close_side,
            order_type=req.order_type, limit_price=req.limit_price,
        ),
        req_type="PLACE_ORDER", symbol=ticker,
    )

    if result.status in ("cancelled", "rejected"):
        raise HTTPException(502, f"Broker did not accept the close order: {result.status}")

    entry = {
        "trade_id":  None,
        "ticker":    ticker,
        "action":    close_side,
        "quantity":  qty,
        "order_id":  result.order_id,
        "status":    result.status,
        "cancelled_open_orders": cancelled,
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "closed_by": "manual_untracked",
    }
    await _log_execution(entry)
    return entry


# ── Execution log ──────────────────────────────────────────────────────────────

@router.get("/execution-log")
async def get_execution_log(limit: int = 50):
    from app.core.database import AsyncSessionLocal
    from app.models.execution_event import ExecutionEvent
    from sqlalchemy import select, func

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(ExecutionEvent)
            .where(ExecutionEvent.kind == "execution")
            .order_by(ExecutionEvent.created_at.desc())
            .limit(limit)
        )).scalars().all()
        total = (await session.execute(
            select(func.count()).where(ExecutionEvent.kind == "execution")
        )).scalar_one()
    return {"log": [r.payload for r in rows], "total": total}


# ── Internal execution helper ──────────────────────────────────────────────────

async def _execute_signal(signal: dict, approved_by: str = "autopilot") -> dict:
    """
    Single fail-closed order pipeline. ALL order entry points must call this.
    Stages (in order — no stage may be skipped):
      1. Kill switch
      1b. Market hours
      1b2. 0DTE autopilot gate (options + approved_by=="autopilot" only)
      1b3. Liquidity / gamma / close-proximity gates (options only, fail-open on missing data)
      1c. Frequency controller (non-manual)
      1d. Strategy health (non-manual; fail-open on error)
      2. Guardrail risk check (fail closed — DB error = refused)
      2b. Portfolio gate — concentration / max positions / heat (Step 8)
      3. Duplicate guard
      3b. Cooldown after close (fail closed on DB error)
      4. Broker submission (+ account mode + margin)
      5. Fill-confirmed recording (CRITICAL alert on failure)
    """
    await asyncio.sleep(0)

    observability.incr("execute.attempt")
    ticker      = signal.get("ticker", "")
    asset_type  = signal.get("asset_type", "equity")
    executed_at = datetime.now(timezone.utc).isoformat()

    def _blocked(reason: str) -> dict:
        observability.incr("execute.blocked")
        observability.event("blocked", ticker=ticker, asset_type=asset_type,
                            reason=reason.split(":")[0])
        return {
            "signal_id":  signal.get("id"),
            "ticker":     ticker,
            "asset_type": asset_type,
            "result":     "blocked",
            "reason":     reason,
            "executed_at": executed_at,
        }

    def _skipped(reason: str) -> dict:
        observability.incr("execute.skipped")
        return {
            "signal_id":  signal.get("id"),
            "ticker":     ticker,
            "asset_type": asset_type,
            "result":     "skipped",
            "reason":     reason,
            "executed_at": executed_at,
        }

    # ── Stage 1: Kill switch ───────────────────────────────────────────────────
    if _is_kill_switch_active():
        logger.warning("Order blocked for %s — kill switch is engaged", ticker)
        return _blocked("kill_switch")

    # ── Stage 1a: EmotionGuard (consecutive-loss pause / tilt) ─────────────────
    # Fail-open: an import error or DB error must never halt trading here.
    try:
        from app.services.unified_risk import check_emotion as _check_emotion
        _eg_allowed, _eg_reason = _check_emotion()
        if not _eg_allowed:
            logger.warning("EmotionGuard blocked %s: %s", ticker, _eg_reason)
            return _blocked(f"emotion_guard: {_eg_reason}")
    except Exception as _eg_exc:
        logger.warning("EmotionGuard check error (fail-open): %s", _eg_exc)

    # ── Stage 1b: Market hours ─────────────────────────────────────────────────
    # Pause order submission outside US regular trading hours. The scanner keeps
    # generating signals 24/7; execution auto-resumes at the next open. Options
    # don't trade after hours and after-hours equity fills are poor, so this
    # covers every path (autopilot, copilot approval, manual).
    from app.core.config import settings as _mh_cfg
    if getattr(_mh_cfg, "market_hours_only", True):
        from app.utils.market_hours import is_market_open, market_status
        if not is_market_open():
            status = market_status()
            logger.info(
                "Order blocked for %s — market closed (%s, %s)",
                ticker, status["reason"], status["now_et"],
            )
            return _blocked(f"market_closed: {status['reason']}")

    # ── Stage 1b2: 0DTE Autopilot Gate ──────────────────────────────────────────
    # "0DTE Autopilot disabled" is documented UI-facing language (see
    # evaluate_options()'s advisory warning) but was never enforced in this,
    # the one authoritative order pipeline every signal source funnels
    # through — a genuinely 0DTE/1DTE options signal approved by AUTOPILOT
    # could previously reach the broker with no DTE-specific stop. COPILOT
    # approval and manual entry are unaffected — 0DTE stays available with a
    # human in the loop, matching the UI's own "Copilot or manual only" text.
    if asset_type == "options" and approved_by == "autopilot":
        _dte = (signal.get("spread") or {}).get("dte")
        if _dte is not None and _dte <= 1:
            logger.warning(
                "Order blocked for %s — 0DTE/1DTE autopilot disabled (dte=%s)",
                ticker, _dte,
            )
            return _blocked("0dte_autopilot_disabled")

    # ── Stage 1b3: Liquidity / gamma / close-proximity gates ────────────────────
    # Real per-leg bid/ask/OI/gamma only exists on the live IBKR chain path
    # (main.py's _live_spread_quote()) — the yfinance/Black-Scholes fallback
    # paths set these to None rather than fabricate a value. Every check here
    # is fail-open on None: a signal is never blocked because live market
    # data merely wasn't available, only because a real reading failed it.
    if asset_type == "options":
        spread = signal.get("spread") or {}

        width_pct = spread.get("bid_ask_width_pct")
        if width_pct is not None and width_pct > 0.15:
            logger.warning(
                "Order blocked for %s — spread too wide (%.1f%% of mid)",
                ticker, width_pct * 100,
            )
            return _blocked(f"liquidity_gate: spread_too_wide ({width_pct:.1%})")

        open_interest = spread.get("open_interest")
        if open_interest is not None and open_interest < 50:
            logger.warning(
                "Order blocked for %s — open interest too low (%s)",
                ticker, open_interest,
            )
            return _blocked(f"liquidity_gate: open_interest_too_low ({open_interest})")

        # 0DTE-specific: no new same-day-expiry entries in the last 30
        # minutes of RTH — a position opened that late has no time left to
        # be managed. Additive to Stage 1b2 above: this one also covers
        # Copilot/manual approval, which 1b2 intentionally doesn't touch.
        dte = spread.get("dte")
        if dte is not None and dte <= 1:
            from app.utils.market_hours import minutes_to_close
            mins_left = minutes_to_close()
            if mins_left is not None and mins_left < 30:
                logger.warning(
                    "Order blocked for %s — 0DTE entry too close to the close (%.0f min left)",
                    ticker, mins_left,
                )
                return _blocked(f"liquidity_gate: 0dte_near_close ({mins_left:.0f}min left)")

    # ── Stage 2: Guardrail risk check (fail closed) ────────────────────────────
    try:
        portfolio_state = await _fetch_portfolio_state()
    except RiskGateError as exc:
        logger.error("Risk gate refused trade for %s (fail closed): %s", ticker, exc)
        return _blocked(f"risk_gate_error: {exc}")

    # ── Stage 1c: Trade Frequency Controller (before the risk engine) ───────────
    # Profitability before activity: per-mode daily cap, min confidence, max risk
    # score, and a positive-EV quality filter. Reuses the portfolio-state read.
    # Manual trades bypass it — the user is overriding signal generation on
    # purpose (they still face the kill switch, market hours, guardrails, and
    # sizing). Equity Desk composer orders are the same category — a human
    # chose ticker/side/entry/stop/target directly, there's no AI signal
    # behind it for a confidence/EV/risk-score gate to meaningfully apply
    # to — but unlike /manual-trade it still queues through Copilot approval,
    # so it's exempted here by source rather than by skipping that queue.
    from app.core.config import settings as _tm_cfg
    is_manual_source = approved_by == "manual" or signal.get("source") == "equity_desk_composer"
    if not is_manual_source and not getattr(_tm_cfg, "execution_test_mode", False):
        from app.services.trade_frequency_controller import trade_frequency_controller
        freq = trade_frequency_controller.evaluate(
            signal, trades_today=portfolio_state.trades_today,
        )
        if not freq.allowed:
            logger.info(
                "Frequency controller blocked %s: %s (score=%.3f risk=%d ev=%.3f)",
                ticker, freq.reason, freq.weighted_score, freq.risk_score, freq.expected_value,
            )
            return _blocked(f"frequency_controller: {freq.reason}")

        # ── Stage 1d: Strategy Health Monitor (auto-suspend) ────────────────────
        # A strategy whose live edge has degraded vs. its baseline accepts no new
        # entries until it recovers. Advisory — fails OPEN on error so a transient
        # DB issue never halts trading (kill switch + guardrails still protect).
        strat = signal.get("strategy", "")
        if strat:
            health = await _strategy_health_for(strat)
            if health is not None and health.status == "degraded":
                logger.warning(
                    "Strategy health blocked %s (%s): %s",
                    ticker, strat, "; ".join(health.reasons),
                )
                return _blocked(
                    f"strategy_health: {strat} degraded — {'; '.join(health.reasons)}")

    _guardrail = GuardrailEngine()
    guardrail_status = _guardrail.check_all(portfolio_state)
    if not guardrail_status.trading_allowed:
        logger.warning("Guardrail blocked %s: %s", ticker, guardrail_status.reason)
        return _blocked(f"guardrail: {guardrail_status.reason}")

    # Capital preservation: restrict strategy selection even when trading is allowed
    strategy = signal.get("strategy", "")
    if strategy and not _guardrail.is_strategy_allowed(strategy, guardrail_status):
        logger.warning(
            "Capital preservation blocked strategy %s for %s", strategy, ticker
        )
        return _blocked(f"capital_preservation: strategy {strategy!r} not allowed in {guardrail_status.trading_mode} mode")

    # ── Stage 2b: Portfolio gate (concentration / max positions / heat) ─────
    # Step 8 — wires RiskManager + portfolio_engine into the live OMS path.
    # Greeks caps remain off unless execution_enforce_portfolio_greeks=true.
    # Rollback: execution_portfolio_gate=false.
    # When max_positions blocks an incoming entry (equity or options) and
    # position_rotation_on_max is enabled, Capital Rotation produces a
    # ROTATION_REVIEW intent for human approval and the signal stops here.
    #
    # It does NOT close anything. Rotation used to call
    # rotate_for_blocked_entry() from this spot, which closed N positions
    # ranked worst-first and then re-checked the gate — so a blocked signal
    # could liquidate held positions automatically, purely to free a slot.
    # Ranking by "most underwater" made that the sunk-cost fallacy
    # mechanised: an existing loss says nothing about a position's remaining
    # prospects, and realising it is an accounting event, not an edge. On
    # 2026-08-28 that path was one scan away from closing MRVL and MSTR for
    # about -$11,384 combined.
    #
    # Replacement now requires explicit approval of a review that compares
    # forward prospects only. This branch is read-only; the close functions
    # additionally refuse any rotation-sourced close that carries no approval
    # token (see position_rotation._assert_rotation_approved), so removing
    # the call here is the first of two independent barriers, not the only one.
    from app.core.config import settings as _rot_cfg
    from app.services.execution_portfolio_gate import check_execution_portfolio
    portfolio_gate = await check_execution_portfolio(
        signal, portfolio_value=float(portfolio_state.current_value or 0),
    )
    if (
        not portfolio_gate.allowed
        and "max_positions" in (portfolio_gate.flags or [])
        and asset_type in ("equity", "options")
        and getattr(_rot_cfg, "position_rotation_on_max", False)
    ):
        try:
            from app.broker.broker_factory import get_broker
            from app.services.position_rotation import propose_rotation_incumbent
            from app.services.rotation_review import PositionFacts, build_rotation_review

            incumbent = await propose_rotation_incumbent(
                incoming_ticker=ticker, broker=get_broker(),
            )
            _tp = signal.get("trade_plan") or {}
            _entry, _stop, _target = (
                _tp.get("entry_price"), _tp.get("stop_price"), _tp.get("target_price"),
            )
            review = build_rotation_review(
                incumbent=(
                    PositionFacts(
                        ticker=incumbent.underlying, side="incumbent",
                        direction=incumbent.spread_type,
                        quality_score=incumbent.quality_score,
                        confidence=incumbent.confidence,
                        in_flagged_cluster=incumbent.in_flagged_cluster,
                        unrealized_pnl_context_only=incumbent.unrealized_pnl,
                    )
                    if incumbent is not None
                    # No eligible incumbent (Winner Protection, or unknown
                    # P&L). An empty-facts incumbent yields
                    # "insufficient_data", which is the honest answer — not a
                    # comparison against a fabricated average position.
                    else PositionFacts(ticker="(none eligible)", side="incumbent")
                ),
                challenger=PositionFacts(
                    ticker=ticker,
                    side="challenger",
                    direction=signal.get("action"),
                    alpha_edge=signal.get("alpha_edge_score"),
                    confidence=signal.get("confidence"),
                    stop_distance=(
                        abs(float(_entry) - float(_stop))
                        if _entry is not None and _stop is not None else None
                    ),
                    target_distance=(
                        abs(float(_target) - float(_entry))
                        if _entry is not None and _target is not None else None
                    ),
                    liquidity_ok=_challenger_liquidity_ok(signal),
                ),
                portfolio_heat_fraction=await _portfolio_heat_fraction(
                    float(portfolio_state.current_value or 0)),
                materiality_margin=float(getattr(
                    _rot_cfg, "rotation_review_materiality_margin", 15.0)),
            )
            entry = {
                "kind": "ROTATION_REVIEW",
                "ticker": ticker,
                "asset_type": asset_type,
                "result": "pending_approval",
                "blocked_by": "max_positions",
                "incumbent_trade_id": incumbent.trade_id if incumbent else None,
                "challenger_signal": signal,
                "review": review,
            }
            await _log_execution(entry)
            # Persisted to the same pending-approval queue the Copilot path
            # uses, so there is one approval concept in this system rather
            # than two. _resolve_pending_approval's atomic pending→resolved
            # flip is what makes an approval single-use.
            await _queue_rotation_review(entry)
        except Exception as _rev_exc:
            # A review is advisory. Failing to build one must not become a
            # path to executing anything, so this only logs — the return
            # below is unconditional.
            logger.error("Rotation review failed for %s: %s", ticker, _rev_exc)
        logger.info(
            "Rotation review raised for %s — awaiting approval, nothing closed", ticker,
        )
        return _skipped("rotation_pending_approval")
    if not portfolio_gate.allowed:
        logger.warning(
            "Portfolio gate blocked %s: %s flags=%s",
            ticker, portfolio_gate.reason, portfolio_gate.flags,
        )
        return _blocked(f"portfolio_gate: {portfolio_gate.reason}")

    # Fast no-op exits should not hit lifecycle or DB gates.
    if asset_type == "equity":
        trade_plan = signal.get("trade_plan", {})
        shares = trade_plan.get("shares", 1)
        if not shares or shares <= 0:
            return _skipped("zero_size")
    elif asset_type == "options":
        quantity = int(signal.get("quantity", 1) or 0)
        if quantity <= 0:
            return _skipped("zero_size")

    # ── Stage 3: Duplicate guard ───────────────────────────────────────────────
    # Key on (underlying, asset class) so SPY equity and SPY options can coexist.
    try:
        from app.core.database import AsyncSessionLocal
        from app.models.trade import Trade
        from app.services.trade_identity import asset_class_from_signal, asset_class_from_trade
        from sqlalchemy import select
        wanted = asset_class_from_signal({**signal, "asset_type": asset_type})
        async with AsyncSessionLocal() as _db:
            rows = (await _db.execute(
                select(Trade).where(
                    Trade.underlying == ticker,
                    Trade.status.in_(["open", "pending"]),
                )
            )).scalars().all()
        existing = next(
            (t for t in rows if asset_class_from_trade(t) == wanted),
            None,
        )
        if existing is not None:
            logger.info(
                "Skipping %s %s — open/pending %s trade already exists in DB",
                ticker, wanted, wanted,
            )
            return _skipped("already_open")

        # The read above cannot see an entry that has been submitted but not
        # yet recorded — the trade row is written only after the broker
        # accepts, so two signals arriving inside that round trip both read
        # zero rows and both pass. Claim the position before submitting, so
        # the second one collides with something that exists.
        from app.services import position_claim
        if not await position_claim.try_claim(
            ticker, wanted, dispatch_id=signal.get("dispatch_id"),
        ):
            return _skipped("entry_in_flight")
    except Exception as _dup_exc:
        # Fail closed, matching Stage 2's guardrail gate (_fetch_portfolio_state)
        # a few lines above — a DB blip here must not silently let a possible
        # duplicate order through the one check meant to catch it.
        logger.error("Duplicate check failed for %s (fail closed): %s", ticker, _dup_exc)
        return _blocked(f"duplicate_check_error: {_dup_exc}")

    # ── Stage 3b: Cooldown after close ──────────────────────────────────────────
    # Any position close (stop, target, manual, rotation) sets a floor before the
    # same (underlying, asset class) can be immediately re-entered — stops a name
    # that just got stopped out from being whipsawed right back in. Keyed
    # identically to Stage 3's duplicate guard via the same asset-class helpers.
    from app.core.config import settings as _cd_cfg
    _cooldown_hours = getattr(_cd_cfg, "position_cooldown_hours", 0)
    if _cooldown_hours > 0:
        try:
            from app.core.database import AsyncSessionLocal
            from app.models.trade import Trade
            from app.services.trade_identity import asset_class_from_signal, asset_class_from_trade
            from sqlalchemy import select
            wanted = asset_class_from_signal({**signal, "asset_type": asset_type})
            async with AsyncSessionLocal() as _db:
                rows = (await _db.execute(
                    select(Trade)
                    .where(
                        Trade.underlying == ticker,
                        Trade.status == "closed",
                        Trade.exit_date.isnot(None),
                    )
                    .order_by(Trade.exit_date.desc())
                )).scalars().all()
            recent_close = next(
                (t for t in rows if asset_class_from_trade(t) == wanted),
                None,
            )
            if recent_close is not None:
                elapsed = datetime.now(timezone.utc) - recent_close.exit_date
                if elapsed < timedelta(hours=_cooldown_hours):
                    logger.info(
                        "Skipping %s %s — closed %s ago, inside %sh cooldown",
                        ticker, wanted, elapsed, _cooldown_hours,
                    )
                    return _skipped("cooldown_active")
        except Exception as _cd_exc:
            # Fail closed, matching Stage 3's own posture a few lines above — a
            # DB blip here must not silently let a possible re-entry through the
            # one check meant to catch it.
            logger.error("Cooldown check failed for %s (fail closed): %s", ticker, _cd_exc)
            return _blocked(f"cooldown_check_error: {_cd_exc}")

    # ── Stages 4+5: Broker submission + fill recording ─────────────────────────
    action = signal.get("action", "")

    try:
        from app.broker.broker_factory import get_broker
        broker = get_broker()

        # ── Account mode guard (fail closed) — last gate before submission ──────
        # Never place a real-money order when the operator believes they're on
        # paper. Verifies the broker's actual account (DU…=paper, U…=live) matches
        # IBKR_TRADING_MODE; blocks on any mismatch or if it can't be verified.
        # Manual orders are NOT exempt — this is a safety floor.
        from app.services.account_guard import verify_account_mode
        _ok, _detail = await verify_account_mode(broker)
        if not _ok:
            logger.critical("Order blocked for %s — account guard: %s", ticker, _detail)
            return _blocked(f"account_guard: {_detail}")

        # ── Live tenure guard (fail closed) — the charter's paper-trading rule ──
        # "Paper trade for 3 months minimum before any live capital." A no-op in
        # paper mode; on a live account it blocks until the track record meets
        # the configured floors. Manual orders are NOT exempt — like the account
        # guard above, this is a safety floor, not a suggestion.
        from app.services.live_tenure_guard import verify_live_tenure
        _ok, _detail = await verify_live_tenure()
        if not _ok:
            logger.critical("Order blocked for %s — live tenure guard: %s", ticker, _detail)
            return _blocked(f"live_tenure_guard: {_detail}")

        # ── Margin guard — block new entries when margin is critical ────────────
        # Fail-OPEN: margin figures are advisory and may be absent (paper); we only
        # block on a positively-detected critical state, never on missing data.
        try:
            from app.services.margin_monitor import evaluate_margin
            from app.core.config import settings as _mg_cfg
            _acct = await ibkr_coordinator.submit(
                Priority.P0, broker.get_account_summary, key="account_summary", req_type="ACCOUNT_SUMMARY",
                timeout=5.0,  # bounded well under the coordinator's 30s default — this
                # runs on every execution attempt, and the surrounding try/except already
                # fails open on any error (including a timeout) per this guard's own design
            )
            if _acct.is_stale:
                # Stale figures cannot clear this guard. IBKR serves account
                # data from a cached push stream, so a dead stream still
                # returns a complete, confident-looking margin picture — the
                # one from whenever it died. Evaluating it risks the guard's
                # only dangerous error: reading "margin is fine" off numbers
                # that predate the blow-up it exists to catch. Treated as
                # absent rather than as a block, which keeps this guard's
                # documented fail-open contract intact (it blocks only on a
                # positively-detected critical state) while refusing to draw
                # an all-clear from data that cannot support one.
                logger.warning(
                    "Margin guard skipped for %s — account values stale (%.0fs old)",
                    ticker, _acct.data_age_seconds or 0.0,
                )
            elif _acct.maintenance_margin is not None:
                _m = evaluate_margin(
                    net_liquidation=float(_acct.net_liquidation or 0),
                    maintenance_margin=float(_acct.maintenance_margin or 0),
                    excess_liquidity=float(_acct.excess_liquidity or 0),
                    buying_power=float(_acct.buying_power or 0),
                    init_margin=float(_acct.init_margin or 0),
                    warn_pct=_mg_cfg.margin_warn_pct,
                    critical_pct=_mg_cfg.margin_critical_pct,
                )
                if _m.status == "critical":
                    logger.warning("Order blocked for %s — margin: %s", ticker, _m.detail)
                    return _blocked(f"margin_critical: {_m.detail}")
        except Exception as _mg_exc:
            logger.debug("Margin guard skipped for %s: %s", ticker, _mg_exc)

        if asset_type == "equity":
            trade_plan = signal.get("trade_plan", {})
            shares     = trade_plan.get("shares", 1)
            entry_price = trade_plan.get("entry_price")

            # Reprice off a LIVE quote just before submission. trade_plan's
            # entry_price is the daily-bar close from scan time
            # (equity_signal_engine.compute_equity_trade_plan) — already
            # stale by the time the order reaches the broker (scans run every
            # 15 min while the market keeps moving). Submitting a DAY LIMIT
            # order at that stale print only fills if price happens to trade
            # back to that exact level; otherwise it sits for the full
            # 30-minute grace window and gets cancelled
            # (order_unfilled_timeout) — confirmed in production: the same
            # ticker retried 2-3 times before one attempt happened to land
            # close enough to the live tape to fill. A small marketable
            # buffer off the live bid/ask fixes this, mirroring the options
            # path's existing preference for a live chain quote over its
            # Black-Scholes estimate.
            try:
                quote = await broker.get_latest_quote(ticker)
                if action == "BUY" and quote.ask_price > 0:
                    entry_price = float(quote.ask_price) * (1 + _EQUITY_LIMIT_BUFFER)
                elif action == "SELL" and quote.bid_price > 0:
                    entry_price = float(quote.bid_price) * (1 - _EQUITY_LIMIT_BUFFER)
            except Exception as _q_exc:
                logger.debug(
                    "Live quote unavailable for %s, using scan-time entry_price: %s",
                    ticker, _q_exc,
                )

            try:
                result = await ibkr_coordinator.submit(
                    Priority.P0,
                    lambda: broker.place_equity_order(
                        ticker=ticker,
                        qty=shares,
                        side=action,
                        order_type=signal.get("order_type", "limit"),
                        limit_price=entry_price,
                        stop=trade_plan.get("stop_price"),
                        take_profit=trade_plan.get("target_price"),
                    ),
                    req_type="PLACE_ORDER", symbol=ticker, timeout=150.0,
                )
            except asyncio.TimeoutError:
                # The coordinator's wait_for gives up, but asyncio.shield()
                # means the real IBKR call keeps running regardless — it can
                # still fill seconds later with nothing here left to record
                # it. Write the same pending row the normal path below would
                # (dispatch_id falls back to signal["id"] exactly like the
                # success path already does when result.order_id is
                # unavailable) so the existing _poll_fills() reconciliation
                # (main.py) promotes it to "open" once the position shows up
                # live, or cancels it after the usual grace period if it
                # never does — instead of the fill silently going untracked.
                from app.services.trade_recorder import trade_recorder
                equity_direction = "equity_short" if action.upper() == "SELL" else "equity_long"
                recorded = await trade_recorder.record_fill(
                    strategy="equity",
                    underlying=ticker,
                    option_type=equity_direction,
                    short_strike=trade_plan.get("entry_price") or 0,
                    long_strike=trade_plan.get("stop_price") or 0,
                    target_price=trade_plan.get("target_price"),
                    expiration=date.today(),
                    quantity=shares,
                    entry_credit=trade_plan.get("entry_price") or 0,
                    signal_score=signal.get("signal_score", 0),
                    iv_rank=signal.get("iv_rank", 0),
                    regime=signal.get("regime", "unknown"),
                    approved_by=approved_by,
                    dispatch_id=signal.get("id", ""),
                    status="pending",
                )
                if recorded is None:
                    logger.critical(
                        "CRITICAL: equity order for %s timed out waiting on the broker "
                        "AND the fallback pending-row write failed — position may be "
                        "untracked. Immediate review required.", ticker,
                    )
                observability.incr("execute.timeout")
                observability.event("timeout", ticker=ticker, asset_type="equity")
                return {
                    "signal_id":  signal.get("id"),
                    "ticker":     ticker,
                    "asset_type": "equity",
                    "result":     "pending_confirmation",
                    "note": (
                        "Broker did not acknowledge in time; a pending position was "
                        "recorded and will be confirmed or cancelled automatically "
                        "once the broker responds."
                    ),
                    "executed_at": executed_at,
                }

            # A terminated-unfilled order is NOT recorded — recording it would
            # create a phantom position the broker never opened.
            if result.status in ("cancelled", "rejected"):
                logger.info("Equity order for %s %s — not recorded", ticker, result.status)
                return _blocked(f"order_{result.status}")

            # Record as a live position only on a confirmed fill; otherwise record
            # as pending so the fill reconciler promotes/cancels it later.
            entry_status = "open" if result.status == "filled" else "pending"

            # Encode direction so exit P&L is computed with the right sign.
            # SELL = short (profit when price falls), default BUY = long.
            equity_direction = "equity_short" if action.upper() == "SELL" else "equity_long"

            # Stage 5: fill recording — CRITICAL on failure (filled but unrecorded is dangerous)
            from app.services.trade_recorder import trade_recorder
            recorded = await trade_recorder.record_fill(
                strategy="equity",
                underlying=ticker,
                option_type=equity_direction,
                short_strike=trade_plan.get("entry_price") or 0,
                long_strike=trade_plan.get("stop_price") or 0,
                target_price=trade_plan.get("target_price"),
                expiration=date.today(),
                quantity=shares,
                entry_credit=trade_plan.get("entry_price") or 0,
                signal_score=signal.get("signal_score", 0),
                iv_rank=signal.get("iv_rank", 0),
                regime=signal.get("regime", "unknown"),
                approved_by=approved_by,
                dispatch_id=result.order_id or signal.get("id", ""),
                status=entry_status,
            )
            if recorded is None:
                logger.critical(
                    "CRITICAL: fill recorded at broker for %s but DB write FAILED — "
                    "position is untracked. Immediate review required.",
                    ticker,
                )

            observability.incr("execute.submitted")
            observability.event("submitted", ticker=ticker, asset_type="equity",
                                order_id=result.order_id)
            return {
                "signal_id":    signal.get("id"),
                "ticker":       ticker,
                "asset_type":   "equity",
                "action":       action,
                "shares":       shares,
                "entry_price":  trade_plan.get("entry_price"),
                "stop_price":   trade_plan.get("stop_price"),
                "target_price": trade_plan.get("target_price"),
                "order_id":     result.order_id,
                "order_status": result.status,
                "result":       "submitted",
                "approved_by":  approved_by,
                "executed_at":  executed_at,
            }

        elif asset_type == "options":
            from app.broker.broker_interface import SpreadOrder, SpreadLeg
            from decimal import Decimal

            spread_data = signal.get("spread", {})
            expiry_str  = spread_data.get("expiration", "")
            short_str   = spread_data.get("short_strike", 0)
            long_str    = spread_data.get("long_strike", 0)
            opt_type    = spread_data.get("option_type", "put")
            credit      = spread_data.get("net_credit", 0)
            strategy    = signal.get("strategy", "bull_put_spread")
            quantity    = int(signal.get("quantity", 1))

            expiry_date = date.fromisoformat(expiry_str) if expiry_str else date.today()
            if "bear_call" in strategy:
                opt_type = "call"

            from app.core.config import settings as _cfg
            aggression = getattr(_cfg, "limit_price_aggression", 1.0)
            # `credit` (net_credit) is a PER-CONTRACT dollar amount (already x100,
            # e.g. $150 for a $1.50 spread). The broker expects a PER-SHARE net
            # price (1.50) — IB applies the x100 multiplier itself. Passing 150
            # asked for a $150 credit per spread and the order NEVER filled, which
            # is why no options trades were ever executed. Convert back to per-share.
            credit_per_share = float(credit) / 100.0
            limit_px   = Decimal(str(round(credit_per_share * aggression, 2)))

            order = SpreadOrder(
                strategy=strategy,
                underlying=ticker,
                legs=[
                    SpreadLeg(symbol=ticker, expiration=expiry_date,
                              strike=Decimal(str(short_str)), option_type=opt_type,
                              action="SELL", quantity=quantity),
                    SpreadLeg(symbol=ticker, expiration=expiry_date,
                              strike=Decimal(str(long_str)), option_type=opt_type,
                              action="BUY", quantity=quantity),
                ],
                limit_price=limit_px,
                time_in_force="DAY",
            )
            try:
                result = await ibkr_coordinator.submit(
                    Priority.P0, lambda: broker.place_order(order),
                    req_type="PLACE_ORDER", symbol=ticker, timeout=150.0,
                )
            except asyncio.TimeoutError:
                # Same lost-fill gap as the equity branch above — see that
                # comment for the full asyncio.shield() explanation.
                from app.services.trade_recorder import trade_recorder
                recorded = await trade_recorder.record_fill(
                    strategy=strategy,
                    underlying=ticker,
                    option_type=opt_type,
                    short_strike=float(short_str),
                    long_strike=float(long_str),
                    expiration=expiry_date,
                    quantity=quantity,
                    entry_credit=credit_per_share,
                    spread_width=abs(float(short_str) - float(long_str)),
                    signal_score=signal.get("signal_score", 0),
                    iv_rank=signal.get("iv_rank", 0),
                    regime=signal.get("regime", "unknown"),
                    approved_by=approved_by,
                    dispatch_id=signal.get("id", ""),
                    status="pending",
                )
                if recorded is None:
                    logger.critical(
                        "CRITICAL: options order for %s %s timed out waiting on the "
                        "broker AND the fallback pending-row write failed — position "
                        "may be untracked. Immediate review required.", strategy, ticker,
                    )
                observability.incr("execute.timeout")
                observability.event("timeout", ticker=ticker, asset_type="options", strategy=strategy)
                return {
                    "signal_id":  signal.get("id"),
                    "ticker":     ticker,
                    "asset_type": "options",
                    "strategy":   strategy,
                    "result":     "pending_confirmation",
                    "note": (
                        "Broker did not acknowledge in time; a pending position was "
                        "recorded and will be confirmed or cancelled automatically "
                        "once the broker responds."
                    ),
                    "executed_at": executed_at,
                }

            # A terminated-unfilled order is NOT recorded (no phantom position).
            if result.status in ("cancelled", "rejected"):
                logger.info("Options order for %s %s %s — not recorded",
                            strategy, ticker, result.status)
                return _blocked(f"order_{result.status}")

            # "filled"/"partial" → a real position exists now → open.
            # "submitted"/"pending" → still working → pending (reconciler resolves).
            entry_status = "open" if result.status in ("filled", "partial") else "pending"

            # Stage 5: fill recording — CRITICAL on failure
            from app.services.trade_recorder import trade_recorder
            recorded = await trade_recorder.record_fill(
                strategy=strategy,
                underlying=ticker,
                option_type=opt_type,
                short_strike=float(short_str),
                long_strike=float(long_str),
                expiration=expiry_date,
                quantity=quantity,
                # Store per-share net credit (1.50, not 150) so record_exit's
                # x100 contract multiplier yields correct dollar P&L.
                entry_credit=credit_per_share,
                spread_width=abs(float(short_str) - float(long_str)),
                signal_score=signal.get("signal_score", 0),
                iv_rank=signal.get("iv_rank", 0),
                regime=signal.get("regime", "unknown"),
                approved_by=approved_by,
                dispatch_id=result.order_id or signal.get("id", ""),
                status=entry_status,
            )
            if recorded is None:
                logger.critical(
                    "CRITICAL: fill recorded at broker for %s %s but DB write FAILED — "
                    "position is untracked. Immediate review required.",
                    strategy, ticker,
                )

            observability.incr("execute.submitted")
            observability.event("submitted", ticker=ticker, asset_type="options",
                                strategy=strategy, order_id=result.order_id)
            return {
                "signal_id":    signal.get("id"),
                "ticker":       ticker,
                "asset_type":   "options",
                "strategy":     strategy,
                "short_strike": short_str,
                "long_strike":  long_str,
                "option_type":  opt_type,
                "expiration":   expiry_str,
                "net_credit":   credit,
                "order_id":     result.order_id,
                "order_status": result.status,
                "result":       "submitted",
                "approved_by":  approved_by,
                "executed_at":  executed_at,
            }

        else:
            return _blocked(f"unknown asset_type: {asset_type}")

    except Exception as exc:
        observability.incr("execute.error")
        observability.event("error", ticker=ticker, asset_type=asset_type, error=str(exc))
        return {
            "signal_id":  signal.get("id"),
            "ticker":     ticker,
            "asset_type": asset_type,
            "result":     "error",
            "error":      str(exc),
            "executed_at": executed_at,
        }


# ── Called by main.py background scanner ─────────────────────────────────────

async def handle_signal(signal: dict) -> None:
    """
    Route a generated signal based on current execution mode.
    Called from the background scanner in main.py after signal scoring.
    """
    mode = execution_mode_manager.mode

    if mode == ExecutionMode.MANUAL:
        return   # Signal already written to _recent_signals — nothing more to do

    elif mode == ExecutionMode.COPILOT:
        await _queue_pending_approval(signal)

    elif mode == ExecutionMode.AUTOPILOT:
        # All risk checks (kill switch, guardrails, duplicate) happen inside _execute_signal.
        # The inline guardrail block that was here is deleted — _execute_signal is the
        # single gate for all paths.
        result = await _execute_signal(signal, approved_by="autopilot")
        if result.get("result") in ("blocked", "skipped"):
            signal["autopilot_blocked"] = result.get("reason")
            logger.warning(
                "Autopilot %s for %s: %s",
                result["result"], signal.get("ticker"), result.get("reason"),
            )
        await _log_execution(result)


# ── Scan panel signal submission ────────────────────────────────────────────────

class ScanSignalRequest(BaseModel):
    """Signal from options/equity scan panel or Equity Desk composer."""
    ticker: str
    action: str
    entry_price: float
    stop_price: float
    target_price: float
    entry_ladder: list = []
    # None (not 0.0/0.1) for a human-composed order with no AI signal behind
    # it — see the equity_desk_composer carve-out in _execute_signal's
    # Stage 1c gate below. Real scan-sourced signals always supply these.
    kelly_fraction: Optional[float] = None
    expected_value: float = 0.0
    pop: float = 0.0
    confidence: Optional[float] = None
    source: str = "scan_engine"  # "options_scan_engine" or "equity_scan_engine"
    # Equity size — composer sends this; scan panel defaults to 1 if omitted.
    shares: int = 1
    asset_type: str = "equity"
    # Options scan / composer — required when asset_type is options
    strategy: Optional[str] = None
    quantity: int = 1
    spread: Optional[dict] = None


def _require_options_spread(req: "ScanSignalRequest") -> dict:
    """Validate options queue payload; return normalized spread dict."""
    spread = dict(req.spread or {})
    short_strike = spread.get("short_strike")
    long_strike = spread.get("long_strike")
    option_type = (spread.get("option_type") or "").lower()
    if short_strike is None or long_strike is None or option_type not in ("put", "call"):
        raise HTTPException(
            status_code=400,
            detail=(
                "options signals require spread with short_strike, long_strike, "
                "and option_type (put|call)"
            ),
        )
    if not spread.get("expiration"):
        raise HTTPException(
            status_code=400,
            detail="options signals require spread.expiration (YYYY-MM-DD)",
        )
    if spread.get("net_credit") is None and spread.get("max_loss") is None:
        raise HTTPException(
            status_code=400,
            detail="options signals require spread.net_credit or spread.max_loss",
        )
    return spread


@router.post("/signal", dependencies=[Depends(require_api_key), Depends(rate_limit), Depends(require_broker_access)])
async def submit_scan_signal(req: ScanSignalRequest):
    """
    Submit a signal from scan panel / Equity Desk for execution routing.

    Routes through execution mode:
    - MANUAL / COPILOT: queue for approval
    - AUTOPILOT: still queues from this endpoint (scan auto-execute disabled)

    Equity payloads include trade_plan.shares so approve → _execute_signal
    does not silently default to 1 share when the composer sent a larger size.

    Options payloads must set asset_type=options and include a defined-risk
    spread; equity-shaped options_scan_engine bodies are rejected.
    """
    signal_id = str(uuid.uuid4())
    source = (req.source or "scan_engine").strip()
    asset_type = (req.asset_type or "equity").lower().strip()
    if source == "options_scan_engine" or asset_type in ("options", "option"):
        asset_type = "options"

    if asset_type == "options":
        spread = _require_options_spread(req)
        strategy = (req.strategy or "bull_put_spread").strip()
        quantity = max(int(req.quantity or 1), 1)
        signal = {
            "id": signal_id,
            "ticker": req.ticker.upper(),
            "action": req.action,
            "asset_type": "options",
            "strategy": strategy,
            "quantity": quantity,
            "spread": spread,
            "entry_price": req.entry_price,
            "stop_price": req.stop_price,
            "target_price": req.target_price,
            "confidence": req.confidence,
            "kelly_fraction": req.kelly_fraction,
            "expected_value": req.expected_value,
            "pop": req.pop,
            "entry_ladder": req.entry_ladder,
            "source": source,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "approved_by": "scan_panel",
        }
        shares = quantity
    else:
        shares = max(int(req.shares or 1), 1)
        # Build signal compatible with _execute_signal()
        signal = {
            "id": signal_id,
            "ticker": req.ticker.upper(),
            "action": req.action,
            "asset_type": "equity",
            "entry_price": req.entry_price,
            "stop_price": req.stop_price,
            "target_price": req.target_price,
            "trade_plan": {
                "shares": shares,
                "entry_price": req.entry_price,
                "stop_price": req.stop_price,
                "target_price": req.target_price,
                "risk_reward": (
                    abs(req.target_price - req.entry_price) / abs(req.entry_price - req.stop_price)
                    if abs(req.entry_price - req.stop_price) > 1e-9 else 0.0
                ),
            },
            "confidence": req.confidence,
            "kelly_fraction": req.kelly_fraction,
            "expected_value": req.expected_value,
            "pop": req.pop,
            "entry_ladder": req.entry_ladder,
            "source": source,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "approved_by": "scan_panel",
        }

    mode = execution_mode_manager.mode

    if mode == ExecutionMode.MANUAL:
        # Queue for user approval
        await _queue_pending_approval(signal)
        return {
            "signal_id": signal_id,
            "ticker": req.ticker,
            "status": "pending_approval",
            "shares": shares,
            "message": f"Signal queued for manual approval (execution mode: MANUAL)",
        }

    elif mode == ExecutionMode.COPILOT:
        # Copilot decides
        await _queue_pending_approval(signal)
        return {
            "signal_id": signal_id,
            "ticker": req.ticker,
            "status": "pending_copilot",
            "shares": shares,
            "message": f"Signal submitted to Copilot (execution mode: COPILOT)",
        }

    elif mode == ExecutionMode.AUTOPILOT:
        # SAFETY: scan-driven autopilot auto-execution is intentionally disabled
        # until the scan execution path has test coverage. The scan engine and
        # panels are live for MANUAL/COPILOT; autopilot placing trades directly
        # from scans is gated off. To re-enable, restore the _execute_signal call
        # below and add tests for the scan → _execute_signal path.
        #   result = await _execute_signal(signal, approved_by="scan_autopilot")
        await _queue_pending_approval(signal)
        logger.info(
            "Scan signal %s for %s queued (autopilot scan-execute disabled pending tests)",
            signal_id, req.ticker,
        )
        return {
            "signal_id": signal_id,
            "ticker": req.ticker,
            "status": "pending_approval",
            "shares": shares,
            "message": "Autopilot scan-execute is disabled pending tests — queued for approval.",
        }

    else:
        raise HTTPException(status_code=400, detail=f"Unknown execution mode: {mode}")
