"""
Backtest routes — wired to the real walk-forward backtester.
Runs in a background task; polls /{run_id}/results for status.
Results are persisted to the backtest_runs DB table so history
survives backend restarts.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.api.tier_deps import clamp_start_date
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, desc

from app.core.database import AsyncSessionLocal
from app.models.backtest_result import BacktestRun

router = APIRouter()

# In-memory cache for active/recent runs (fast polling)
_runs: dict[str, dict] = {}


class BacktestRunRequest(BaseModel):
    strategy:              str
    start_date:            str
    end_date:              str
    starting_capital:      float = 25000.0
    signal_score_override: float = 0.70


class BacktestCompareRequest(BaseModel):
    start_date:       str
    end_date:         str
    starting_capital: float = 25000.0


class EquityBacktestRunRequest(BaseModel):
    ticker:            str
    start_date:        str
    end_date:          str
    starting_capital:  float = 25000.0


async def _persist_run(run_id: str, data: dict) -> None:
    """Save or update a backtest run in the DB."""
    try:
        async with AsyncSessionLocal() as session:
            existing = await session.get(BacktestRun, uuid.UUID(run_id))
            if existing:
                existing.results = {k: v for k, v in data.items()
                                    if k not in ("trades", "equity_curve", "bar_log")}
                existing.metrics = {
                    "win_rate":          data.get("win_rate"),
                    "total_trades":      data.get("total_trades"),
                    "total_pnl":         data.get("total_pnl"),
                    "sharpe_ratio":      data.get("sharpe_ratio"),
                    "max_drawdown_pct":  data.get("max_drawdown_pct"),
                    "annualized_return": data.get("annualized_return"),
                    "profit_factor":     data.get("profit_factor"),
                    "status":            data.get("status"),
                    "completed_at":      data.get("completed_at"),
                    "error":             data.get("error"),
                }
            else:
                row = BacktestRun(
                    id=uuid.UUID(run_id),
                    strategy=data.get("strategy", "unknown"),
                    start_date=datetime.fromisoformat(data["start_date"]).date()
                               if data.get("start_date") else datetime.now().date(),
                    end_date=datetime.fromisoformat(data["end_date"]).date()
                             if data.get("end_date") else datetime.now().date(),
                    starting_capital=data.get("starting_capital", 25000),
                    results={k: v for k, v in data.items()
                             if k not in ("trades", "equity_curve", "bar_log")},
                    metrics={
                        "win_rate":          data.get("win_rate"),
                        "total_trades":      data.get("total_trades"),
                        "total_pnl":         data.get("total_pnl"),
                        "sharpe_ratio":      data.get("sharpe_ratio"),
                        "max_drawdown_pct":  data.get("max_drawdown_pct"),
                        "annualized_return": data.get("annualized_return"),
                        "profit_factor":     data.get("profit_factor"),
                        "status":            data.get("status"),
                        "completed_at":      data.get("completed_at"),
                        "error":             data.get("error"),
                    },
                )
                session.add(row)
            await session.commit()
    except Exception as exc:
        # DB errors should not break the API response
        import logging
        logging.getLogger(__name__).warning("Failed to persist backtest run %s: %s", run_id, exc)


async def _execute_backtest(run_id: str, req: BacktestRunRequest) -> None:
    """Background task that runs the real backtester and stores results."""
    _runs[run_id]["status"] = "running"
    try:
        from app.broker.broker_factory import get_broker
        from app.services.data_fetcher import DataFetcher
        from app.services.backtester import Backtester

        broker  = get_broker()
        fetcher = DataFetcher(broker)
        bt      = Backtester(fetcher)

        result = await bt.run(
            strategy_name=req.strategy,
            start_date=req.start_date,
            end_date=req.end_date,
            starting_capital=req.starting_capital,
            signal_score_override=req.signal_score_override,
        )

        m         = result.metrics
        total_pnl = result.ending_capital - result.starting_capital
        _runs[run_id].update({
            "status":            "completed",
            "completed_at":      datetime.now(timezone.utc).isoformat(),
            "strategy":          result.strategy,
            "start_date":        str(result.start_date),
            "end_date":          str(result.end_date),
            "starting_capital":  result.starting_capital,
            "ending_capital":    result.ending_capital,
            "total_pnl":         round(total_pnl, 2),
            "total_trades":      m.total_trades if m else len(result.trades),
            "winning_trades":    m.winning_trades if m else 0,
            "losing_trades":     m.losing_trades if m else 0,
            "win_rate":          round(m.win_rate, 4) if m else 0.0,
            "total_return_pct":  round(m.total_return_pct, 4) if m else 0.0,
            "annualized_return": round(m.annualized_return_pct, 4) if m else 0.0,
            "max_drawdown_pct":  round(m.max_drawdown_pct, 4) if m else 0.0,
            "sharpe_ratio":      round(m.sharpe_ratio, 4) if m else 0.0,
            "sortino_ratio":     round(m.sortino_ratio, 4) if m else 0.0,
            "profit_factor":     round(m.profit_factor, 4) if m else 0.0,
            "avg_hold_days":     round(m.avg_hold_days, 1) if m else 0.0,
            "expectancy":        round(m.expectancy, 2) if m else 0.0,
            "equity_curve":      result.equity_curve[:500],
            "trades": [
                {
                    "id":           t.id,
                    "entry_date":   str(t.entry_date),
                    "exit_date":    str(t.exit_date),
                    "strategy":     t.strategy,
                    "short_strike": t.short_strike,
                    "long_strike":  t.long_strike,
                    "pnl":          round(t.pnl, 2),
                    "pnl_pct":      round(t.pnl_pct, 4),
                    "exit_reason":  t.exit_reason,
                    "signal_score": t.signal_score,
                    "hold_days":    t.hold_days,
                }
                for t in (result.trades or [])
            ],
        })
    except Exception as exc:
        _runs[run_id].update({"status": "failed", "error": str(exc)})

    # Persist to DB regardless of success/failure
    await _persist_run(run_id, _runs[run_id])


@router.post("/run")
async def run_backtest(req: BacktestRunRequest, request: Request):
    """Kick off a backtest run. Poll GET /{run_id}/results for status."""
    # Clamped to the caller's tier before anything reads it, so the run
    # record, the engine call and the echoed start_date cannot disagree.
    req = req.model_copy(update={
        "start_date": clamp_start_date(request, req.start_date)})
    valid = {"bull_put_spread", "bear_call_spread", "iron_condor", "bull_call_debit_spread"}
    if req.strategy not in valid:
        raise HTTPException(400, f"Unknown strategy. Valid: {sorted(valid)}")

    run_id = str(uuid.uuid4())
    initial = {
        "run_id":     run_id,
        "status":     "queued",
        "strategy":   req.strategy,
        "start_date": req.start_date,
        "end_date":   req.end_date,
        "queued_at":  datetime.now(timezone.utc).isoformat(),
    }
    _runs[run_id] = initial

    # Persist the queued record immediately so it shows in history
    await _persist_run(run_id, initial)

    asyncio.create_task(_execute_backtest(run_id, req))
    return {**_runs[run_id], "message": "Poll GET /api/backtest/{run_id}/results for status."}


async def _execute_equity_backtest(run_id: str, req: EquityBacktestRunRequest) -> None:
    """Background task — mirrors _execute_backtest but drives run_equity()
    and the equity trade fields (direction/entry_price/exit_price/stop/target)
    instead of the options-spread ones."""
    _runs[run_id]["status"] = "running"
    try:
        from app.broker.broker_factory import get_broker
        from app.services.data_fetcher import DataFetcher
        from app.services.backtester import Backtester

        broker  = get_broker()
        fetcher = DataFetcher(broker)
        bt      = Backtester(fetcher)

        result = await bt.run_equity(
            ticker=req.ticker,
            start_date=req.start_date,
            end_date=req.end_date,
            starting_capital=req.starting_capital,
        )

        m         = result.metrics
        total_pnl = result.ending_capital - result.starting_capital
        _runs[run_id].update({
            "status":            "completed",
            "completed_at":      datetime.now(timezone.utc).isoformat(),
            "strategy":          result.strategy,
            "ticker":            req.ticker.upper(),
            "start_date":        str(result.start_date),
            "end_date":          str(result.end_date),
            "starting_capital":  result.starting_capital,
            "ending_capital":    result.ending_capital,
            "total_pnl":         round(total_pnl, 2),
            "total_trades":      m.total_trades if m else len(result.trades),
            "winning_trades":    m.winning_trades if m else 0,
            "losing_trades":     m.losing_trades if m else 0,
            "win_rate":          round(m.win_rate, 4) if m else 0.0,
            "total_return_pct":  round(m.total_return_pct, 4) if m else 0.0,
            "annualized_return": round(m.annualized_return_pct, 4) if m else 0.0,
            "max_drawdown_pct":  round(m.max_drawdown_pct, 4) if m else 0.0,
            "sharpe_ratio":      round(m.sharpe_ratio, 4) if m else 0.0,
            "sortino_ratio":     round(m.sortino_ratio, 4) if m else 0.0,
            "profit_factor":     round(m.profit_factor, 4) if m else 0.0,
            "avg_hold_days":     round(m.avg_hold_days, 1) if m else 0.0,
            "expectancy":        round(m.expectancy, 2) if m else 0.0,
            "equity_curve":      result.equity_curve[:500],
            "bar_log":           result.bar_log[:500],
            "trades": [
                {
                    "id":           t.id,
                    "entry_date":   str(t.entry_date),
                    "exit_date":    str(t.exit_date),
                    "strategy":     t.strategy,
                    "direction":    t.direction,
                    "entry_price":  t.entry_price,
                    "exit_price":   t.exit_price,
                    "stop_price":   t.stop_price,
                    "target_price": t.target_price,
                    "shares":       t.contracts,
                    "pnl":          round(t.pnl, 2),
                    "pnl_pct":      round(t.pnl_pct, 4),
                    "exit_reason":  t.exit_reason,
                    "signal_score": t.signal_score,
                    "hold_days":    t.hold_days,
                }
                for t in (result.trades or [])
            ],
        })
    except Exception as exc:
        _runs[run_id].update({"status": "failed", "error": str(exc)})

    await _persist_run(run_id, _runs[run_id])


@router.post("/run-equity")
async def run_equity_backtest(req: EquityBacktestRunRequest, request: Request):
    """
    Kick off a walk-forward equity backtest for any ticker — reuses the
    live equity_signal_engine scoring + GuardrailEngine, not a parallel
    DSL. Poll GET /{run_id}/results for status; the run also lists in
    GET /history since it's persisted through the same BacktestRun model
    (strategy column stores "equity:{TICKER}").
    """
    # Clamped to the caller's tier before anything reads it, so the run
    # record, the engine call and the echoed start_date cannot disagree.
    req = req.model_copy(update={
        "start_date": clamp_start_date(request, req.start_date)})
    if not req.ticker or not req.ticker.strip():
        raise HTTPException(400, "ticker is required")

    run_id = str(uuid.uuid4())
    initial = {
        "run_id":     run_id,
        "status":     "queued",
        "strategy":   f"equity:{req.ticker.upper()}",
        "ticker":     req.ticker.upper(),
        "start_date": req.start_date,
        "end_date":   req.end_date,
        "queued_at":  datetime.now(timezone.utc).isoformat(),
    }
    _runs[run_id] = initial
    await _persist_run(run_id, initial)

    asyncio.create_task(_execute_equity_backtest(run_id, req))
    return {**_runs[run_id], "message": "Poll GET /api/backtest/{run_id}/results for status."}


@router.get("/{run_id}/results")
async def get_backtest_results(run_id: str):
    # Check memory first (fastest for in-progress runs)
    if run_id in _runs:
        return _runs[run_id]

    # Fall back to DB for runs from previous sessions
    try:
        async with AsyncSessionLocal() as session:
            row = await session.get(BacktestRun, uuid.UUID(run_id))
            if row:
                data = {"run_id": str(row.id), **(row.results or {}), **(row.metrics or {})}
                _runs[run_id] = data  # cache it
                return data
    except Exception:
        pass

    raise HTTPException(404, "Backtest run not found")


@router.get("/history")
async def get_backtest_history(limit: int = 20):
    """Return backtest history from DB (survives restarts)."""
    try:
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                select(BacktestRun)
                .order_by(desc(BacktestRun.created_at))
                .limit(limit)
            )).scalars().all()

            total = (await session.execute(
                select(BacktestRun)
            )).scalars()
            total_count = len(list(total))

        runs = []
        for r in rows:
            m = r.metrics or {}
            res = r.results or {}
            runs.append({
                "run_id":       str(r.id),
                "status":       m.get("status", res.get("status", "unknown")),
                "strategy":     r.strategy,
                "start_date":   str(r.start_date),
                "end_date":     str(r.end_date),
                "total_pnl":    m.get("total_pnl"),
                "win_rate":     m.get("win_rate"),
                "queued_at":    r.created_at.isoformat() if r.created_at else None,
                "completed_at": m.get("completed_at"),
            })

        return {"runs": runs, "total": total_count}

    except Exception as exc:
        # DB unavailable — fall back to in-memory
        runs = sorted(_runs.values(), key=lambda r: r.get("queued_at", ""), reverse=True)
        return {
            "runs": [
                {
                    "run_id":       r["run_id"],
                    "status":       r["status"],
                    "strategy":     r.get("strategy"),
                    "start_date":   r.get("start_date"),
                    "end_date":     r.get("end_date"),
                    "total_pnl":    r.get("total_pnl"),
                    "win_rate":     r.get("win_rate"),
                    "queued_at":    r.get("queued_at"),
                    "completed_at": r.get("completed_at"),
                }
                for r in runs[:limit]
            ],
            "total": len(_runs),
            "warning": f"DB unavailable ({exc}) — showing in-memory only",
        }


@router.post("/compare")
async def compare_strategies(req: BacktestCompareRequest, request: Request):
    """Run all 4 strategies on the same date range in parallel."""
    # Clamped to the caller's tier before anything reads it, so the run
    # record, the engine call and the echoed start_date cannot disagree.
    req = req.model_copy(update={
        "start_date": clamp_start_date(request, req.start_date)})
    strategies = ["bull_put_spread", "bear_call_spread", "iron_condor", "bull_call_debit_spread"]
    run_ids = []
    for strategy in strategies:
        run_req = BacktestRunRequest(
            strategy=strategy,
            start_date=req.start_date,
            end_date=req.end_date,
            starting_capital=req.starting_capital,
        )
        run_id = str(uuid.uuid4())
        initial = {
            "run_id":     run_id,
            "status":     "queued",
            "strategy":   strategy,
            "start_date": req.start_date,
            "end_date":   req.end_date,
            "queued_at":  datetime.now(timezone.utc).isoformat(),
        }
        _runs[run_id] = initial
        await _persist_run(run_id, initial)
        asyncio.create_task(_execute_backtest(run_id, run_req))
        run_ids.append(run_id)

    return {
        "status":  "running",
        "run_ids": run_ids,
        "message": "Poll each run_id via GET /api/backtest/{run_id}/results",
    }


@router.get("/baseline-comparison")
async def baseline_comparison(symbol: str, start: str, end: str, starting_capital: float = 25000.0):
    """
    Research-only comparison of our equity signal engine against simple
    rule-based baselines (Buy & Hold, SMA cross, RSI threshold, MACD cross)
    over one symbol's history. Stateless -- computed on demand, nothing
    persisted, same pattern as the Scenario Lab forecast endpoint.
    """
    from app.services.baseline_comparison_engine import generate_comparison

    try:
        return await asyncio.to_thread(generate_comparison, symbol, start, end, starting_capital)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"comparison data unavailable: {exc}") from exc
