"""
Equity trading API routes.
All signals pass through the UnifiedRiskEngine before execution.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from app.broker.broker_factory import get_broker
from app.core.config import settings
from app.services.equity_signal_engine import (
    compute_equity_trade_plan,
    compute_indicators,
    earnings_gate,
    score_equity_signal,
)
from app.services.iv_overlay import get_iv_signal_boost
from app.services.orderflow_engine import get_orderflow_score

router = APIRouter()

# In-memory signal store (replace with DB queries for production)
_recent_signals: list[dict] = []


async def _yf_bars_route(ticker: str, limit: int = 120):
    """Fetch bars from yfinance — no broker connection required."""
    import asyncio
    import yfinance as yf
    from decimal import Decimal
    from app.broker.broker_interface import Bar

    loop = asyncio.get_running_loop()

    def _fetch():
        hist = yf.Ticker(ticker).history(period=f"{min(limit * 2, 730)}d", auto_adjust=True)
        hist = hist.tail(limit)
        result = []
        for ts, row in hist.iterrows():
            result.append(Bar(
                timestamp=ts.to_pydatetime(),
                open=Decimal(str(round(float(row["Open"]), 4))),
                high=Decimal(str(round(float(row["High"]), 4))),
                low=Decimal(str(round(float(row["Low"]), 4))),
                close=Decimal(str(round(float(row["Close"]), 4))),
                volume=int(row.get("Volume", 0) or 0),
            ))
        return result

    return await loop.run_in_executor(None, _fetch)


@router.post("/scan")
async def scan_equity_signals(tickers: list[str] = None, limit: int = 10):
    """
    A-grade multi-asset equity scan with EV ranking.

    Integrates:
    - Multi-asset scanning (tickers param overrides watchlist)
    - Expected Value ranking (POP × max_profit − (1−POP) × max_loss)
    - Entry ladder logic (kelly-scaled tranches)
    - IV rank + implied move awareness
    - Earnings gate + NO-TRADE gate status

    Returns high-EV candidates ranked by edge, ready for autopilot or manual execution.
    """
    from app.services.equity_scan_engine import scan_options

    # Use provided tickers or fall back to configured watchlist
    if tickers is None:
        tickers = settings.get_equity_watchlist()

    # Broker is optional — only needed for orderflow/IV boost
    try:
        broker = get_broker()
    except Exception:
        broker = None

    result = await scan_options(
        tickers=tickers,
        limit=limit,
        broker=broker,
    )

    return {
        "scanned": len(result.tickers_scanned or []),
        "candidates": [c.as_dict() for c in result.candidates],
        "gate_blocked": result.gate_blocked,
        "gate_reason": result.gate_reason,
        "iv_rank": result.iv_rank,
        "realized_vol": result.realized_vol,
        "error": result.error,
        "tickers_scanned": result.tickers_scanned or [],
    }


@router.get("/signals")
async def list_equity_signals(limit: int = 150):
    """
    Return recent equity signals, deduped to one (most recent) entry per
    ticker.

    _recent_signals holds one raw entry per ticker PER SCAN CYCLE (every
    equity_signal_interval_minutes), newest-first — once more than one
    cycle has run, the same ticker appears multiple times with different
    ids, which rendered as duplicate cards in the frontend and inflated
    the "total"/"actionable" counts. Dedupe by first-seen ticker (== most
    recent, since the list is newest-first) before slicing, matching the
    pattern already used by flow_recommender._bias_from_signals and
    EquityScanPanel.tsx's client-side workaround.

    Default bumped from 50 — the same "stale, unexamined default" pattern
    the watchlist itself had. A single scan cycle across the equity
    watchlist (102 tickers as of the Nasdaq-100 switch) produces one entry
    per ticker; a limit of 50 silently truncated a full cycle to half of
    it. 150 comfortably covers the current watchlist with room to grow,
    while staying under the 200-entry store cap (_recent_signals) so
    nothing already recorded gets hidden.
    """
    seen: set[str] = set()
    deduped: list[dict] = []
    for sig in _recent_signals:
        ticker = sig.get("ticker")
        if ticker and ticker in seen:
            continue
        if ticker:
            seen.add(ticker)
        deduped.append(sig)
    return {"signals": deduped[:limit], "total": len(deduped)}


@router.get("/positions")
async def get_equity_positions():
    """Return open equity positions from the active broker."""
    try:
        broker = get_broker()
        if hasattr(broker, "get_equity_positions"):
            positions = await broker.get_equity_positions()
        else:
            positions = []
        return {"positions": positions, "broker": settings.broker}
    except Exception as exc:
        return {"positions": [], "error": str(exc), "broker": settings.broker}


@router.get("/chart/{ticker}")
async def get_equity_chart(
    ticker: str,
    timeframe: str = Query("15m", pattern="^(5m|15m|1h|1d)$"),
    limit: int = Query(96, ge=20, le=240),
):
    """
    Return recent OHLCV bars for the chart workstation UI.

    Uses yfinance so the chart remains available even when IBKR is offline.
    """
    import asyncio
    import yfinance as yf

    ticker = ticker.upper().strip()
    loop = asyncio.get_running_loop()

    interval_map = {
        "5m":  ("5d", "5m"),
        "15m": ("10d", "15m"),
        "1h":  ("60d", "60m"),
        "1d":  ("1y", "1d"),
    }
    period, interval = interval_map[timeframe]

    def _fetch():
        hist = yf.Ticker(ticker).history(period=period, interval=interval, auto_adjust=True)
        hist = hist.tail(limit)
        bars: list[dict] = []
        for ts, row in hist.iterrows():
            bars.append({
                "timestamp": ts.to_pydatetime().isoformat(),
                "open": round(float(row["Open"]), 4),
                "high": round(float(row["High"]), 4),
                "low": round(float(row["Low"]), 4),
                "close": round(float(row["Close"]), 4),
                "volume": int(row.get("Volume", 0) or 0),
            })
        return bars

    try:
        bars = await loop.run_in_executor(None, _fetch)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not load chart data for {ticker}: {exc}") from exc

    if not bars:
        raise HTTPException(status_code=404, detail=f"No chart data available for {ticker}")

    closes = [bar["close"] for bar in bars]
    highs = [bar["high"] for bar in bars]
    lows = [bar["low"] for bar in bars]

    return {
        "ticker": ticker,
        "timeframe": timeframe,
        "bars": bars,
        "summary": {
            "last": closes[-1],
            "change_pct": round(((closes[-1] / closes[0]) - 1) * 100, 2) if closes[0] else 0.0,
            "high": max(highs),
            "low": min(lows),
        },
    }
