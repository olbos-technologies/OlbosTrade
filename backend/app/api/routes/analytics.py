"""
Analytics API routes — performance breakdown by trading mode.
"""

import uuid
from datetime import date
from typing import Optional
from fastapi import APIRouter, Query
from starlette.requests import HTTPConnection

from app.api.scope_deps import scope_or_401
from app.core.database import AsyncSessionLocal
from app.services.mode_analytics import ModeAnalyticsEngine, ModeTrade
from app.services.trade_scope import owned_by
from app.core.config import settings

router = APIRouter()
engine = ModeAnalyticsEngine(starting_capital=settings.starting_capital)


async def _load_trades_from_db(
    org_id: Optional[uuid.UUID],
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
) -> list[ModeTrade]:
    """Load one owner's closed trades and convert to ModeTrade records.

    `org_id` is a required positional, and deliberately NOT resolved in here.
    Everything below runs inside a bare `except Exception: return []`, so a
    401 raised in this function would be swallowed and an unauthenticated
    caller would receive an empty analytics page instead of a refusal — a
    failed read wearing the costume of a safe value. Each route resolves the
    scope before calling, outside that try.

    Passing `None` means the single-operator scope (the rows migration 0041
    left unattributed), not "every row".
    """
    try:
        from app.models.trade import Trade
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            query = select(Trade).where(owned_by(org_id), Trade.status == "closed")
            result = await session.execute(query)
            db_trades = result.scalars().all()

        trades = []
        for t in db_trades:
            # Skip closes with unknown P&L (pnl=NULL) — counting them as $0 would
            # pollute win rate, drawdown and Sharpe with a fabricated flat trade.
            if t.pnl is None:
                continue
            entry = t.entry_date.date() if t.entry_date else date.today()
            exit_d = t.exit_date.date() if t.exit_date else None

            if date_from and entry < date_from:
                continue
            if date_to and entry > date_to:
                continue

            hold = 0
            if exit_d and entry:
                hold = (exit_d - entry).days

            trades.append(ModeTrade(
                trade_id=str(t.id),
                mode=str(t.trading_mode_at_entry or "balanced"),
                strategy=str(t.strategy),
                pnl=float(t.pnl or 0),
                pnl_pct=float(t.pnl_pct or 0),
                entry_date=entry,
                exit_date=exit_d,
                hold_days=hold,
                signal_score=float(t.signal_score or 0),
                exit_reason=str(t.exit_reason or ""),
                regime=t.regime,
            ))
        return trades
    except Exception as exc:
        return []


@router.get("/by-mode")
async def get_analytics_by_mode(
    conn: HTTPConnection,
    date_from: Optional[date] = Query(None),
    date_to:   Optional[date] = Query(None),
):
    """
    Full performance breakdown by trading mode.
    Returns win rate, P&L, Sharpe, expectancy for each mode.
    """
    async with AsyncSessionLocal() as session:
        org_id = await scope_or_401(session, conn)
    trades = await _load_trades_from_db(org_id, date_from, date_to)
    comparison = engine.compute_comparison(trades, date_from, date_to)

    return {
        "generated_at":    comparison.generated_at.isoformat(),
        "date_range":      comparison.date_range,
        "total_trades":    comparison.total_trades,
        "best_mode":       comparison.best_mode,
        "recommendation":  comparison.recommendation,
        "modes": {
            mode: {
                "display_name":       s.display_name,
                "trade_count":        s.trade_count,
                "win_rate":           s.win_rate,
                "win_count":          s.win_count,
                "loss_count":         s.loss_count,
                "avg_win":            s.avg_win,
                "avg_loss":           s.avg_loss,
                "expectancy":         s.expectancy,
                "profit_factor":      s.profit_factor,
                "total_pnl":          s.total_pnl,
                "avg_pnl_per_trade":  s.avg_pnl_per_trade,
                "total_return_pct":   s.total_return_pct,
                "max_drawdown_pct":   s.max_drawdown_pct,
                "sharpe_ratio":       s.sharpe_ratio,
                "avg_hold_days":      s.avg_hold_days,
                "avg_signal_score":   s.avg_signal_score,
                "score_vs_outcome":   s.score_vs_outcome,
                "exit_breakdown": {
                    "profit_target": s.profit_target_exits,
                    "stop_loss":     s.stop_loss_exits,
                    "dte_exit":      s.dte_exits,
                    "other":         s.other_exits,
                },
                "last_5_pnl":       s.last_5_trades_pnl,
                "is_improving":     s.is_improving,
                "ui_color":         s.ui_color,
                "recommendation":   s.recommendation,
            }
            for mode, s in comparison.modes.items()
        }
    }


@router.get("/mode/{mode_name}")
async def get_mode_detail(mode_name: str, conn: HTTPConnection):
    """Deep dive into a single mode's performance."""
    async with AsyncSessionLocal() as session:
        org_id = await scope_or_401(session, conn)
    trades = await _load_trades_from_db(org_id)
    mode_trades = [t for t in trades if t.mode == mode_name]

    if not mode_trades:
        return {
            "mode": mode_name,
            "message": f"No trades recorded for {mode_name} mode yet.",
            "trade_count": 0,
        }

    comparison = engine.compute_comparison(mode_trades)
    stats = comparison.modes.get(mode_name)
    if not stats:
        return {"mode": mode_name, "trade_count": 0}

    return {
        "mode":           mode_name,
        "stats":          stats.__dict__,
        "trades_sample":  [
            {
                "date":         str(t.entry_date),
                "pnl":          t.pnl,
                "signal_score": t.signal_score,
                "exit_reason":  t.exit_reason,
                "hold_days":    t.hold_days,
            }
            for t in sorted(mode_trades, key=lambda t: t.entry_date, reverse=True)[:20]
        ]
    }


@router.get("/performance")
async def get_performance(conn: HTTPConnection):
    """
    Portfolio-wide performance over closed trades.

    Loads every closed trade, including the ones whose exit price was never
    captured, and lets compute_performance() partition them — it reports
    unmeasured_trades/measured_pct/performance_incomplete alongside the
    ratios. Filtering the unmeasured rows out here in SQL (as this route
    used to) made them invisible to the very code that exists to disclose
    them, so the figures read as covering the whole book when they did not.
    """
    from app.services.performance_analytics import compute_performance
    from app.models.trade import Trade
    from sqlalchemy import select

    # Outside the try below for the same reason _load_trades_from_db takes its
    # scope as an argument: that except returns a zeroed performance object,
    # which would present a refusal as a flat, measured book.
    async with AsyncSessionLocal() as session:
        org_id = await scope_or_401(session, conn)

    try:
        async with AsyncSessionLocal() as session:
            trades = (await session.execute(
                select(Trade).where(owned_by(org_id), Trade.status == "closed")
            )).scalars().all()
        # `strategy` is what lets compute_performance set reconciler-adopted
        # positions aside; without it they are folded into the headline P&L
        # against a placeholder cost basis. See trade_attribution.
        rows = [{"pnl": float(t.pnl) if t.pnl is not None else None,
                 "strategy": t.strategy,
                 "entry_date": t.entry_date, "exit_date": t.exit_date}
                for t in trades]
    except Exception as exc:
        return {"error": str(exc),
                **compute_performance([], settings.starting_capital)}

    return compute_performance(rows, settings.starting_capital)


def _bucket_by_score(trades: list) -> dict:
    """Bucket trades by signal_score range and show avg P&L per bucket —
    factored out so both the top-level result and the by_regime breakdown
    reuse the exact same bucketing, not a second copy of it."""
    buckets = {
        "0.00-0.65 (below threshold)": [],
        "0.65-0.70 (marginal)":        [],
        "0.70-0.75 (moderate)":        [],
        "0.75-0.80 (good)":            [],
        "0.80-1.00 (excellent)":       [],
    }
    for t in trades:
        s = t.signal_score
        if s < 0.65:
            buckets["0.00-0.65 (below threshold)"].append(t.pnl)
        elif s < 0.70:
            buckets["0.65-0.70 (marginal)"].append(t.pnl)
        elif s < 0.75:
            buckets["0.70-0.75 (moderate)"].append(t.pnl)
        elif s < 0.80:
            buckets["0.75-0.80 (good)"].append(t.pnl)
        else:
            buckets["0.80-1.00 (excellent)"].append(t.pnl)

    bucket_stats = {}
    for label, pnls in buckets.items():
        if pnls:
            wins = [p for p in pnls if p > 0]
            bucket_stats[label] = {
                "trade_count": len(pnls),
                "win_rate":    round(len(wins) / len(pnls), 3),
                "avg_pnl":     round(sum(pnls) / len(pnls), 2),
                "total_pnl":   round(sum(pnls), 2),
            }
        else:
            bucket_stats[label] = {
                "trade_count": 0, "win_rate": 0, "avg_pnl": 0, "total_pnl": 0
            }
    return bucket_stats


@router.get("/signal-score-impact")
async def get_signal_score_impact(conn: HTTPConnection, regime: Optional[str] = Query(None)):
    """
    Shows whether higher signal scores actually predict better outcomes.
    This validates whether the AI model is working.

    Score correlation > 0.2  = model has predictive value
    Score correlation < 0    = model is backwards (bad signal)
    Score correlation ≈ 0    = model has no predictive value

    ``regime`` filters to one market regime before bucketing. Omit it to
    also get a ``by_regime`` breakdown — the same bucket shape computed
    once per distinct regime present in the data, added alongside the
    unfiltered ``by_score_bucket`` rather than a separate ledger endpoint.
    """
    async with AsyncSessionLocal() as session:
        org_id = await scope_or_401(session, conn)
    trades = await _load_trades_from_db(org_id)
    if regime:
        trades = [t for t in trades if t.regime == regime]
    if len(trades) < 10:
        return {
            "message": f"Need at least 10 trades — you have {len(trades)}.",
            "correlation": None,
        }

    bucket_stats = _bucket_by_score(trades)

    # Overall correlation
    corr = engine._score_outcome_correlation(trades)

    verdict = (
        "✓ Model has predictive value" if corr > 0.2
        else "⚠ Model signal is weak — more data needed" if corr > 0
        else "✗ Model may be backwards — investigate"
    )

    result = {
        "overall_correlation": round(corr, 4),
        "verdict": verdict,
        "by_score_bucket": bucket_stats,
        "interpretation": (
            "Positive correlation means higher signal scores predict "
            "better trade outcomes. Target > 0.20 after 50+ trades."
        )
    }

    if not regime:
        regimes = sorted({t.regime for t in trades if t.regime})
        result["by_regime"] = {
            r: _bucket_by_score([t for t in trades if t.regime == r])
            for r in regimes
        }

    return result
