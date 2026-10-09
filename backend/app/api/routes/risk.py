"""
Risk monitoring routes.
FIX #11: Kill switch route now fully implemented — cancels orders, flattens positions.
"""
from __future__ import annotations

from datetime import date, datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, and_, case

from app.api.deps import require_api_key, require_api_key_configured
from app.api.rate_limit import rate_limit
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.reconciliation_snapshot import ReconciliationSnapshot
from app.models.trade import Trade
from app.models.risk_state import PortfolioSnapshot

# ibkr_coordinator's own default (30s) is too slow for these polled UI
# routes — get_account_summary() can stall for the full default when the
# broker connection is degraded, making the route itself look hung.
ACCOUNT_SUMMARY_TIMEOUT_SECONDS = 5.0
from app.services.kill_switch import kill_switch_service
from app.services.position_reconciler import PositionReconciler, ReconciliationError


router = APIRouter()


class KillSwitchResetRequest(BaseModel):
    authorization_code: str


def _serialize_reconciliation_snapshot(snapshot: ReconciliationSnapshot) -> dict:
    return {
        "status": snapshot.status,
        "clean": snapshot.clean,
        "source": snapshot.source,
        "broker_name": snapshot.broker_name,
        "broker_position_count": snapshot.broker_position_count,
        "db_open_trade_count": snapshot.db_open_trade_count,
        "untracked_at_broker": snapshot.untracked_at_broker or [],
        "phantom_in_db": snapshot.phantom_in_db or [],
        "quantity_mismatches": snapshot.quantity_mismatches or [],
        "warnings": snapshot.warnings or [],
        "error_message": snapshot.error_message,
        "checked_at": snapshot.checked_at.isoformat(),
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
    }


@router.get("/portfolio-state")
async def get_portfolio_state():
    """
    Real portfolio state: account value from broker + guardrail metrics from DB.
    """
    try:
        from app.broker.broker_factory import get_broker
        from app.broker.ibkr_coordinator import Priority, ibkr_coordinator
        broker = get_broker()
        acct   = await ibkr_coordinator.submit(
            Priority.P0, broker.get_account_summary, key="account_summary", req_type="ACCOUNT_SUMMARY",
            timeout=ACCOUNT_SUMMARY_TIMEOUT_SECONDS,
        )
        acct_value    = float(acct.net_liquidation)
        buying_power  = float(acct.buying_power)
        cash          = float(acct.cash_balance)
    except Exception as exc:
        acct_value   = settings.starting_capital
        buying_power = settings.starting_capital
        cash         = settings.starting_capital
        broker_error = str(exc)
    else:
        broker_error = None

    try:
        today = date.today()
        week_start  = today - timedelta(days=today.weekday())
        month_start = today.replace(day=1)

        async with AsyncSessionLocal() as session:
            def _pnl_window(from_date: date):
                return select(func.coalesce(func.sum(Trade.pnl), 0)).where(
                    and_(Trade.status == "closed", func.date(Trade.exit_date) >= from_date)
                )

            daily_pnl   = float((await session.execute(_pnl_window(today))).scalar() or 0)
            weekly_pnl  = float((await session.execute(_pnl_window(week_start))).scalar() or 0)
            monthly_pnl = float((await session.execute(_pnl_window(month_start))).scalar() or 0)

            trades_today = int((await session.execute(
                select(func.count(Trade.id)).where(
                    and_(Trade.status == "closed", func.date(Trade.exit_date) == today)
                )
            )).scalar() or 0)

            open_count = int((await session.execute(
                select(func.count(Trade.id)).where(Trade.status == "open")
            )).scalar() or 0)

            # Consecutive losses — count backwards from most recent closed trade
            recent = (await session.execute(
                select(Trade.pnl).where(Trade.status == "closed")
                .order_by(Trade.exit_date.desc()).limit(20)
            )).scalars().all()
            consecutive_losses = 0
            for p in recent:
                if (p or 0) < 0:
                    consecutive_losses += 1
                else:
                    break

        daily_loss_pct   = abs(daily_pnl)   / settings.starting_capital if daily_pnl   < 0 else 0.0
        weekly_loss_pct  = abs(weekly_pnl)  / settings.starting_capital if weekly_pnl  < 0 else 0.0
        monthly_loss_pct = abs(monthly_pnl) / settings.starting_capital if monthly_pnl < 0 else 0.0
        total_pnl        = acct_value - settings.starting_capital

        return {
            "state": {
                "account_value":       acct_value,
                "cash":                cash,
                "buying_power":        buying_power,
                "starting_capital":    settings.starting_capital,
                "total_pnl":           round(total_pnl, 2),
                "return_pct":          round(total_pnl / settings.starting_capital * 100, 2),
                "daily_pnl":           round(daily_pnl, 2),
                "weekly_pnl":          round(weekly_pnl, 2),
                "monthly_pnl":         round(monthly_pnl, 2),
                "daily_loss_pct":      round(daily_loss_pct, 4),
                "weekly_loss_pct":     round(weekly_loss_pct, 4),
                "monthly_loss_pct":    round(monthly_loss_pct, 4),
                "open_positions":      open_count,
                "trades_today":        trades_today,
                "consecutive_losses":  consecutive_losses,
                "max_daily_loss_pct":  settings.max_daily_loss_pct,
                "max_weekly_loss_pct": settings.max_weekly_loss_pct,
                "capital_pct_remaining": round(acct_value / settings.starting_capital, 4),
                **({"broker_error": broker_error} if broker_error else {}),
            }
        }
    except Exception as exc:
        return {"state": {"error": str(exc)}}


@router.get("/daily-pnl")
async def get_daily_pnl():
    """Today's realised P&L from closed trades."""
    try:
        async with AsyncSessionLocal() as session:
            today = date.today()
            daily_pnl = float((await session.execute(
                select(func.coalesce(func.sum(Trade.pnl), 0)).where(
                    and_(Trade.status == "closed", func.date(Trade.exit_date) == today)
                )
            )).scalar() or 0)

        daily_pnl_pct = round(daily_pnl / settings.starting_capital * 100, 4)
        return {
            "daily_pnl":     round(daily_pnl, 2),
            "daily_pnl_pct": daily_pnl_pct,
            "date":          today.isoformat(),
        }
    except Exception as exc:
        return {"daily_pnl": 0, "daily_pnl_pct": 0, "error": str(exc)}


@router.get("/approval/{trade_id}")
async def get_trade_approval(trade_id: str):
    """Check risk approval status for a trade."""
    try:
        from app.models.position import Position
        import uuid as _uuid
        async with AsyncSessionLocal() as session:
            pos = await session.get(Position, _uuid.UUID(trade_id))
        if pos:
            return {
                "trade_id": trade_id,
                "approved": pos.risk_approved,
                "flags":    pos.risk_flags or {},
            }
        return {"trade_id": trade_id, "approved": False, "reason": "not found"}
    except Exception as exc:
        return {"trade_id": trade_id, "approved": False, "error": str(exc)}


@router.get("/reconciliation/latest")
async def get_latest_reconciliation():
    """Return the most recent persisted reconciliation snapshot."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ReconciliationSnapshot)
            .order_by(ReconciliationSnapshot.checked_at.desc())
            .limit(1)
        )
        snapshot = result.scalar_one_or_none()

    return {
        "snapshot": _serialize_reconciliation_snapshot(snapshot) if snapshot else None
    }


@router.get("/reconciliation/history")
async def get_reconciliation_history(limit: int = 20):
    """Return recent reconciliation snapshots for operator review."""
    limit = max(1, min(limit, 100))
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ReconciliationSnapshot)
            .order_by(ReconciliationSnapshot.checked_at.desc())
            .limit(limit)
        )
        rows = result.scalars().all()

    return {
        "snapshots": [_serialize_reconciliation_snapshot(row) for row in rows]
    }


@router.post("/reconciliation/run", dependencies=[Depends(require_api_key), Depends(rate_limit)])
async def run_reconciliation():
    """
    Force a fresh reconciliation against the active broker and persist the result.
    """
    from app.broker.broker_factory import get_broker

    reconciler = PositionReconciler(get_broker())
    try:
        result = await reconciler.reconcile_and_record(source="operator")
        return {
            "status": "clean" if not result.warnings else "warning",
            "result": {
                "clean": result.clean,
                "broker_position_count": result.broker_position_count,
                "db_open_trade_count": result.db_open_trade_count,
                "untracked_at_broker": result.untracked_at_broker,
                "phantom_in_db": result.phantom_in_db,
                "quantity_mismatches": result.quantity_mismatches,
                "warnings": result.warnings,
                "checked_at": result.checked_at.isoformat(),
            },
        }
    except ReconciliationError as exc:
        return {
            "status": "error",
            "error": str(exc),
            "result": {
                "clean": False,
                "broker_position_count": exc.result.broker_position_count if exc.result else 0,
                "db_open_trade_count": exc.result.db_open_trade_count if exc.result else 0,
                "untracked_at_broker": exc.result.untracked_at_broker if exc.result else [],
                "phantom_in_db": exc.result.phantom_in_db if exc.result else [],
                "quantity_mismatches": exc.result.quantity_mismatches if exc.result else [],
                "warnings": exc.result.warnings if exc.result else [],
                "checked_at": exc.result.checked_at.isoformat() if exc.result else None,
            },
        }


@router.get("/kill-switch/status")
async def get_kill_switch_status():
    """Returns current kill switch state."""
    return kill_switch_service.status


@router.post("/kill-switch/trigger", dependencies=[Depends(require_api_key_configured), Depends(rate_limit)])
async def trigger_kill_switch(
    reason: str = "manual",
):
    """
    FIX #11: Fully implemented kill switch.
    Cancels all open orders, flattens all positions, pauses scheduler.
    This is irreversible until manually reset via /kill-switch/reset.
    Requires X-Api-Key header matching SECRET_KEY.
    """
    if kill_switch_service.is_engaged:
        return {
            "status": "already_engaged",
            "detail": kill_switch_service.status,
        }

    result = await kill_switch_service.engage(reason=reason)

    if result.get("errors"):
        # Kill switch ran but had partial errors — still engaged, log for review
        raise HTTPException(
            status_code=207,  # Multi-status
            detail={
                "message": "Kill switch engaged with errors — manual review required",
                "result": result,
            },
        )

    return {
        "status": "engaged",
        "message": "Kill switch engaged. All orders cancelled and positions flattened.",
        "result": result,
    }


@router.post("/kill-switch/engage")
async def engage_kill_switch_ui(reason: str = "manual_ui"):
    """
    Operator-facing kill switch for the dashboard (emergency stop button).

    Unlike /kill-switch/trigger (API-key protected, for external/programmatic
    callers), this path is protected by the app's own auth layer (nginx Basic
    Auth in front of the whole dashboard) so the authenticated operator can hit
    the button in an emergency without embedding the secret key in the browser.
    The UI requires hold-to-confirm friction to prevent accidental clicks.
    Same effect: cancel all orders, flatten positions, pause the scheduler.
    """
    if kill_switch_service.is_engaged:
        return {"status": "already_engaged", "detail": kill_switch_service.status}

    result = await kill_switch_service.engage(reason=reason)
    if result.get("errors"):
        raise HTTPException(
            status_code=207,
            detail={"message": "Kill switch engaged with errors — manual review required",
                    "result": result},
        )
    return {
        "status": "engaged",
        "message": "Kill switch engaged. All orders cancelled and positions flattened.",
        "result": result,
    }


@router.post("/kill-switch/reset")
async def reset_kill_switch(body: KillSwitchResetRequest):
    """
    Reset kill switch after manual review.
    Requires authorization_code matching KILL_SWITCH_RESET_CODE (server env).
    The code must never be embedded in the frontend bundle.
    """
    result = await kill_switch_service.reset(body.authorization_code)
    if not result.get("reset"):
        raise HTTPException(status_code=403, detail=result)
    return result


# ── Scenario / stress analysis + parametric VaR (Phase 2 Batch 4) ──────────────────
async def _fetch_spot(symbol: str) -> float:
    """Live last-price lookup via yfinance — same primitive already used by
    income_screener.py and unusual_activity.py, wrapped for async use the
    same way main.py's _yf_bars wraps its own sync yfinance call."""
    import asyncio as _asyncio
    import yfinance as yf

    loop = _asyncio.get_running_loop()

    def _fetch():
        tk = yf.Ticker(symbol)
        try:
            return float(tk.fast_info["last_price"])
        except Exception:
            h = tk.history(period="1d")
            if h.empty:
                raise ValueError(f"no price data for {symbol}")
            return float(h["Close"].iloc[-1])

    return await loop.run_in_executor(None, _fetch)


def _trade_to_scenario_position(t, spot: float, spot_iv: float = 0.25) -> dict:
    """
    Turn a stored trade into a scenario position using its real live spot
    (caller resolves this per-underlying). IV/rate stay flat for options —
    real per-position IV needs a live options-chain lookup, deliberately
    deferred (same reasoning as Alerts' unwired iv_rank/iv_percentile).

    spread_type is "equity_long"/"equity_short" for equity trades, or
    "call"/"put" for options — never a strategy name (that's t.strategy).
    Same equity-vs-option detection convention as position_risk_dollars()
    in portfolio_engine.py.
    """
    spread_type = (getattr(t, "spread_type", "") or "").lower()
    is_equity = (getattr(t, "strategy", "") == "equity") or spread_type.startswith("equity")
    qty = int(t.quantity or 1)

    if is_equity:
        # Direction lives in the string, not quantity sign — same
        # convention trade_recorder.py/trade_desk.py/main.py already use.
        signed = -qty if spread_type == "equity_short" else qty
        return {
            "symbol": t.underlying, "kind": "equity",
            "spot": spot, "quantity": signed, "multiplier": 1,
        }

    # Options: spread_type is "call"/"put" here, not a strategy-name
    # string — this short/long derivation is a pre-existing approximation
    # (single synthetic leg from short_strike only) left unchanged.
    short = spread_type.startswith(("bull_put", "bear_call", "iron"))
    signed = -qty if short else qty
    strike = float(t.short_strike or 0) or 100.0
    option_type = "call" if spread_type.startswith("c") else "put"
    from datetime import date as _date
    dte = max(0, (t.expiration - _date.today()).days) if t.expiration else 0
    return {
        "symbol": t.underlying, "kind": "option",
        "option_type": option_type,
        "spot": spot, "strike": strike, "dte_days": dte,
        "iv": spot_iv, "r": 0.04, "quantity": signed, "multiplier": 100,
    }


@router.get("/scenarios")
async def get_scenarios():
    """Stress the open book under the standard shock set (crash, vol spike, …)."""
    import asyncio

    from app.services import spot_price_cache
    from app.services.scenario_engine import run_all

    try:
        async with AsyncSessionLocal() as session:
            open_trades = (await session.execute(
                select(Trade).where(Trade.status == "open")
            )).scalars().all()

        underlyings = sorted({t.underlying for t in open_trades})
        excluded_symbols: list[dict] = []
        sem = asyncio.Semaphore(5)

        async def _resolve(symbol: str):
            async with sem:
                try:
                    spot, _status = await spot_price_cache.get_spot(
                        symbol, lambda: _fetch_spot(symbol)
                    )
                    return symbol, spot
                except Exception as exc:
                    excluded_symbols.append({"ticker": symbol, "reason": f"spot unavailable: {exc}"})
                    return symbol, None

        resolved = await asyncio.gather(*(_resolve(u) for u in underlyings))
        spot_by_underlying = {sym: spot for sym, spot in resolved if spot is not None}

        positions = [
            _trade_to_scenario_position(t, spot_by_underlying[t.underlying])
            for t in open_trades if t.underlying in spot_by_underlying
        ]
        result = run_all(positions, capital=settings.starting_capital)
        result["excluded_symbols"] = excluded_symbols
        return result
    except Exception as exc:
        return {"error": str(exc), "scenarios": [], "worst_scenario": None, "worst_pnl": 0.0}


@router.get("/var")
async def get_var(confidence: float = 0.95, horizon_days: int = 1):
    """Parametric (delta-vega-normal) portfolio VaR / Expected Shortfall."""
    from app.services import spot_price_cache
    from app.services.portfolio_risk_sim import portfolio_var

    net_delta = net_vega = 0.0
    vol = 0.18
    try:
        from app.main import _greeks_tracker, _current_regime
        if _greeks_tracker:
            net_delta = _greeks_tracker.net_delta() * 100.0   # contract → share-delta
            net_vega = _greeks_tracker.net_vega() * 100.0
        feat = getattr(_current_regime, "features_used", None)
        if feat:
            vol = max(0.05, float(getattr(feat, "vix", 18.0)) / 100.0)
    except Exception:
        pass

    try:
        from app.broker.broker_factory import get_broker
        from app.broker.ibkr_coordinator import Priority, ibkr_coordinator
        acct = await ibkr_coordinator.submit(
            Priority.P0, get_broker().get_account_summary, key="account_summary", req_type="ACCOUNT_SUMMARY", timeout=ACCOUNT_SUMMARY_TIMEOUT_SECONDS,
        )
        pv = float(acct.net_liquidation)
    except Exception:
        pv = settings.starting_capital

    try:
        spot, spot_status = await spot_price_cache.get_spot("SPY", lambda: _fetch_spot("SPY"))
    except Exception as exc:
        return {
            "available": False, "reason": f"spot price unavailable: {exc}",
            "confidence": confidence, "horizon_days": horizon_days,
            "var": None, "expected_shortfall": None, "var_pct": None, "es_pct": None,
        }

    result = portfolio_var(net_delta, net_vega, spot, vol, pv,
                            confidence=confidence, horizon_days=horizon_days)
    return {
        "available": True, "spot_price": spot, "spot_source": "SPY",
        "spot_data_status": spot_status, **result,
    }


@router.get("/margin")
async def get_margin():
    """
    Margin utilization / buying-power-reduction status from broker figures.

    Returns the margin monitor's status (ok/warn/critical) plus the raw figures.
    `available: false` when the broker doesn't report margin (e.g. disconnected).
    """
    from app.services.margin_monitor import evaluate_margin

    try:
        from app.broker.broker_factory import get_broker
        from app.broker.ibkr_coordinator import Priority, ibkr_coordinator
        acct = await ibkr_coordinator.submit(
            Priority.P0, get_broker().get_account_summary, key="account_summary", req_type="ACCOUNT_SUMMARY", timeout=ACCOUNT_SUMMARY_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        return {"available": False, "reason": str(exc)}

    if acct.maintenance_margin is None:
        return {"available": False, "reason": "broker did not report margin figures"}

    status = evaluate_margin(
        net_liquidation=float(acct.net_liquidation or 0),
        maintenance_margin=float(acct.maintenance_margin or 0),
        excess_liquidity=float(acct.excess_liquidity or 0),
        buying_power=float(acct.buying_power or 0),
        init_margin=float(acct.init_margin or 0),
        warn_pct=settings.margin_warn_pct,
        critical_pct=settings.margin_critical_pct,
    )
    # Age travels with the figures. The utilization number reads as a live
    # measurement whether or not it still is one, so the reader needs the
    # clock alongside it to tell a current 57% from a 57% that stopped
    # updating an hour ago.
    return {
        "available": True,
        **status.to_dict(),
        "data_age_seconds": acct.data_age_seconds,
        "is_stale": acct.is_stale,
    }


@router.get("/reconciliation")
async def get_reconciliation():
    """
    Broker-vs-DB position reconciliation status (non-raising).

    Surfaces untracked broker positions (held at broker, no OlbosTrade record),
    DB phantoms (open in DB, not at broker), and quantity mismatches so the UI
    can flag a "needs reconcile" state without halting trading.
    """
    from app.broker.broker_factory import get_broker
    from app.services.position_reconciler import PositionReconciler

    res = await PositionReconciler(get_broker()).check()
    return {
        "clean":                 res.clean,
        "broker_position_count": res.broker_position_count,
        "db_open_trade_count":   res.db_open_trade_count,
        "untracked_at_broker":   res.untracked_at_broker,
        "phantom_in_db":         res.phantom_in_db,
        "warnings":              res.warnings,
        "checked_at":            res.checked_at.isoformat(),
    }
