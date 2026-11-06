"""Options analytics routes — on-demand spread intelligence for the UI."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

router = APIRouter()

# In-memory options signal store (mirrors equity._recent_signals).
# Populated by the background options scanner in main._run_options_scan.
_recent_options_signals: list[dict] = []


class SpreadAnalyzeRequest(BaseModel):
    spot: float
    short_strike: float
    long_strike: float
    option_type: str = "put"        # put = bull put | call = bear call
    dte: float = 30
    iv: float = 0.20
    credit_per_share: float = 0.60
    r: float = 0.05


@router.get("/signals")
async def list_options_signals(limit: int = 150):
    """
    Return recent options spread signals (most recent first).

    Default bumped from 50 — the same "stale, unexamined default" pattern
    the watchlist itself had. A single scan cycle across the equity
    watchlist (102 tickers as of the Nasdaq-100 switch) can produce up to
    one entry per ticker; a limit of 50 silently truncated a full cycle to
    half of it. 150 comfortably covers the current watchlist with room for
    it to grow, while staying under the 200-entry store cap
    (_recent_options_signals) so nothing already recorded gets hidden.
    """
    return {
        "signals": _recent_options_signals[:limit],
        "total": len(_recent_options_signals),
    }


@router.post("/signals/scan")
async def trigger_options_signal_scan():
    """
    Compute one options signal preview cycle across the full equity
    watchlist (not just SPY/QQQ — matches what the background scanner now
    covers) — same strategy classification, strikes, and Greeks, but
    execute=False so this on-demand "show me current signals" UI action can
    never itself dispatch to handle_signal()/order submission. Only the
    scheduled background scanner (main.py's _background_scheduler) executes.
    """
    # Lazy imports — main imports this module at startup.
    from app.core.config import settings
    from app.main import _run_options_scan

    for symbol in settings.get_equity_watchlist():
        try:
            await _run_options_scan(symbol, execute=False)
        except Exception:
            # Keep going so one symbol's failure doesn't blank the rest.
            pass

    return {
        # Same fix as GET /signals — this scan just recorded up to one entry
        # per watchlist ticker (102 as of the Nasdaq-100 switch); a hardcoded
        # :50 here truncated the "RUN SCAN" button's own response to half a
        # cycle regardless of what GET /signals' own limit was.
        "signals": _recent_options_signals[:150],
        "total": len(_recent_options_signals),
    }


def _history_row_to_dict(r) -> dict:
    return {
        "id":             str(r.id),
        "ticker":         r.ticker,
        "strategy":       r.strategy,
        "action":         r.action,
        "confidence":     float(r.confidence),
        "pop":            float(r.pop) if r.pop is not None else None,
        "kelly_fraction": float(r.kelly_fraction) if r.kelly_fraction is not None else None,
        "signal_score":   float(r.signal_score),
        "quantity":       r.quantity,
        "iv_rank":        float(r.iv_rank),
        "regime":         r.regime,
        "spread": {
            "option_type":  r.option_type,
            "short_strike": float(r.short_strike),
            "long_strike":  float(r.long_strike),
            "expiration":   r.expiration.isoformat(),
            "dte":          r.dte,
            "net_credit":   float(r.net_credit),
            "max_loss":     float(r.max_loss),
            "breakeven":    float(r.breakeven),
        },
        "sigma":         float(r.sigma),
        "vix_used":      float(r.vix_used),
        "credit_source": r.credit_source,
        "evidence":      r.evidence,
        "intelligence":  r.intelligence,
        "generated_at":  r.generated_at.isoformat() if r.generated_at else None,
    }


@router.get("/signals/history")
async def get_options_signal_history(
    limit: int = Query(200, le=1000),
    strategy: Optional[str] = Query(None),
    ticker: Optional[str] = Query(None),
):
    """
    Persisted options signal log, most recent first — survives a backend
    restart, unlike GET /signals (the in-memory feed). No status/outcome
    fields: options spreads have no forward-resolution logic yet, this is
    a signal log, not a win-rate study (see /api/signal-research for that
    model, equity-only today).
    """
    from sqlalchemy import func, select
    from app.core.database import AsyncSessionLocal
    from app.models.options_signal_history import OptionsSignalHistory

    filters = []
    if strategy:
        filters.append(OptionsSignalHistory.strategy == strategy)
    if ticker:
        filters.append(OptionsSignalHistory.ticker == ticker.upper())

    stmt = select(OptionsSignalHistory).order_by(OptionsSignalHistory.generated_at.desc()).limit(limit)
    count_stmt = select(func.count()).select_from(OptionsSignalHistory)
    for f in filters:
        stmt = stmt.where(f)
        count_stmt = count_stmt.where(f)

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(stmt)).scalars().all()
        total = (await session.execute(count_stmt)).scalar_one()

    return {"signals": [_history_row_to_dict(r) for r in rows], "total": total}


def _funnel_context() -> tuple[str, Optional[dict], Optional[dict]]:
    """The live facts the funnel reads alongside the database: execution mode, the regime the
    scan is gated on, and the frequency controller's floors for the active trading mode.
    Each is best-effort: a missing one is reported as null, never invented."""
    mode = "unknown"
    regime: Optional[dict] = None
    rule: Optional[dict] = None
    try:
        from app.services.execution_mode import execution_mode_manager
        mode = execution_mode_manager.mode.value
    except Exception:
        pass
    try:
        from app.main import _current_regime
        if _current_regime is not None:
            regime = {
                "name": _current_regime.regime.value,
                "options_allowed": bool(_current_regime.options_allowed),
                "strategies_allowed": list(_current_regime.strategies_allowed or []),
                "signal_threshold": _current_regime.signal_threshold,
            }
    except Exception:
        pass
    try:
        from app.services.trade_frequency_controller import STRATEGY_MIN_POP, rule_for
        from app.services.trading_mode import trading_mode_manager
        active = trading_mode_manager.current.active_mode
        r = rule_for(active)
        rule = {"trading_mode": active.value, "min_confidence": r.min_confidence, "min_pop": r.min_pop,
                "max_risk_score": r.max_risk_score, "hard_max_per_day": r.hard_max_per_day,
                "credit_spread_min_pop": STRATEGY_MIN_POP.get("bull_put_spread")}
    except Exception:
        pass
    return mode, regime, rule


@router.get("/funnel")
async def options_funnel(days: int = Query(7, ge=1, le=90)):
    """Where do options candidates die?  Scan rejections, qualified signals, copilot queue and
    execution outcomes for the last `days`, with one-sentence verdict. Read-only.

    A database failure is a 500, not a page of zeros: "nothing was rejected" and "we could not
    look" must never read the same."""
    from app.core.database import AsyncSessionLocal
    from app.services.options_funnel import build_funnel

    mode, regime, rule = _funnel_context()
    async with AsyncSessionLocal() as session:
        return await build_funnel(session, days=days, mode=mode, regime=regime, frequency_rule=rule)


@router.post("/analyze")
async def analyze(req: SpreadAnalyzeRequest):
    """Return POP / prob-touch / expected move / Greeks / EV / Kelly for a spread."""
    from app.services.options_intelligence import analyze_spread

    try:
        intel = analyze_spread(
            spot=req.spot, short_strike=req.short_strike, long_strike=req.long_strike,
            option_type=req.option_type, dte=req.dte, iv=req.iv,
            credit_per_share=req.credit_per_share, r=req.r,
        )
        return intel.as_dict()
    except ValueError as exc:
        return {"error": str(exc)}


@router.post("/scan")
async def scan_options_spreads(
    tickers: list[str] = None,
    strategy: str = "bull_put_spread",
    limit: int = 10,
):
    """
    A-grade multi-ticker options scan with live chain pricing + entry ladder logic.

    Ranks spreads by Expected Value (EV) with:
    - Live IBKR chain pricing (fallback: yfinance → Black-Scholes)
    - Entry ladder logic (kelly-scaled tranches)
    - IV rank + skew adjustments
    - NO-TRADE gates (kill switch, market hours)

    Returns high-EV candidates ready for autopilot or manual execution.
    """
    from app.services.options_scan_engine import scan_options

    if tickers is None:
        tickers = ["SPY", "ES", "QQQ"]

    result = await scan_options(
        tickers=tickers,
        strategy=strategy,
        limit=limit,
        base_quantity=1,
    )

    return {
        "scanned": len(result.tickers_scanned or []),
        "candidates": [c.as_dict() for c in result.candidates],
        "gate_blocked": result.gate_blocked,
        "gate_reason": result.gate_reason,
        "spot": result.spot,
        "vix_estimate": result.vix_estimate,
        "realized_vol": result.realized_vol,
        "iv_rank": result.iv_rank,
        "error": result.error,
        "tickers_scanned": result.tickers_scanned or [],
    }
