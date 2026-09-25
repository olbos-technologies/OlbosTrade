"""
Crypto API — phase 1, read-only.

Every route here reads or recomputes signals. None of them can open, size or
close a position, because the scan they expose (crypto_scan.run_crypto_scan)
has no path to the order layer. The one write in the whole flow is the
signal_outcomes row that makes the forward-outcome record possible.

These endpoints sit behind the app-wide session requirement mounted in
main.py — same as /api/equity — so nothing here is added to the public
allowlist.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

router = APIRouter()


@router.get("/watchlist")
async def get_crypto_watchlist() -> dict:
    """
    The scanned symbols and the posture they are scanned under.

    ``execution`` is reported explicitly rather than left to be inferred. A
    client showing crypto signals next to equity signals would otherwise have no
    way to tell that one of the two can be acted on automatically and the other
    cannot, which is exactly the confusion worth spending a field to prevent.
    """
    from app.core.config import settings
    from app.services.crypto_signal_engine import CRYPTO_SCORING_VERSION, to_alpaca_symbol

    symbols = settings.get_crypto_watchlist()
    return {
        "enabled": bool(settings.crypto_enabled),
        "execution": "disabled",
        "phase": 1,
        "symbols": [
            {"symbol": s, "alpaca_symbol": to_alpaca_symbol(s)} for s in symbols
        ],
        "count": len(symbols),
        "min_confidence": settings.crypto_min_confidence,
        "scan_interval_minutes": settings.crypto_signal_interval_minutes,
        "max_position_pct": settings.crypto_max_position_pct,
        "engine_version": CRYPTO_SCORING_VERSION,
        "data_source": "yfinance daily bars",
    }


@router.get("/signals")
async def get_crypto_signals(
    limit: int = Query(50, ge=1, le=200),
    action: str | None = Query(None, description="BUY | SELL | HOLD"),
    routable_only: bool = Query(False),
) -> dict:
    """
    Most recent crypto signals from the background scan, newest first.

    In-memory and wiped on restart — the durable record is signal_outcomes,
    served by /api/signal-research/outcomes?asset_type=crypto. This is the live
    feed, including HOLDs, which the durable record deliberately excludes.
    """
    from app.core.config import settings
    from app.services.crypto_scan import recent_crypto_signals

    signals = recent_crypto_signals(limit=200)
    if action:
        wanted = action.strip().upper()
        signals = [s for s in signals if s.get("action") == wanted]
    if routable_only:
        signals = [s for s in signals if s.get("routable")]

    return {
        "enabled": bool(settings.crypto_enabled),
        "execution": "disabled",
        "signals": signals[:limit],
        "total": len(signals),
    }


@router.post("/scan")
async def trigger_crypto_scan() -> dict:
    """
    Run a crypto scan now and return its summary.

    Exists so the pipeline can be verified on demand instead of waiting out a
    scan interval — which matters most right after a deploy, when the question
    is whether the thing runs at all. Returns the same counters the background
    job logs, including the skip reasons.
    """
    from app.services.crypto_scan import run_crypto_scan

    return await run_crypto_scan()
