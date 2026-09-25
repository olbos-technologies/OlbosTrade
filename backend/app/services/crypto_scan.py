"""
Crypto signal scan — phase 1, read-only.

THE ONE THING THIS MODULE GUARANTEES
------------------------------------
It generates crypto signals and records them. It does not trade. There is no
import of handle_signal, no import of trade_frequency_controller's gate, and no
broker order call anywhere in this file — so "crypto cannot place an order" is a
property of the code, not a flag someone can flip by accident. A later execution
phase has to add that path deliberately, in a change that can be reviewed as
what it is.

WHY READ-ONLY FIRST
-------------------
The system already has a forward-outcome recorder and resolver for equities
(signal_outcome_tracker.py). Pointing crypto at it costs nothing in risk and
produces the one thing no amount of backtest or promo screenshot can: a dated,
deduplicated record of what this engine predicted for crypto and what price
actually did next, with a denominator. Until that exists there is nothing to
size a crypto position against.

WHAT IS SHARED WITH THE EQUITY SCAN, AND WHAT IS NOT
----------------------------------------------------
Shared, deliberately: compute_indicators, score_equity_signal,
compute_equity_trade_plan, compute_opportunity_score, record_signal. These are
pure functions over OHLCV plus one DB write; forking them for crypto would
create two copies of the same math to drift apart.

Not shared, and each omission is a decision:

  * earnings_gate      — no analogue. Skipped.
  * orderflow_score    — no crypto feed. The scoring function's neutral default
                         (0.0) is used, so a crypto signal is scored as an
                         equity signal would be with orderflow unavailable.
  * regime gating      — _current_regime is SPY/VIX-derived. Crypto is not
                         gated on it, and does not claim it: rows are stamped
                         CRYPTO_REGIME_UNCLASSIFIED and carry an explicit
                         neutral regime_score so the ranking factor is a stated
                         0.5 rather than an accident of a lookup miss.
  * market hours       — crypto trades 24/7. Nothing here consults
                         market_hours.py, which is correct for a scan that
                         places no orders, and is one of the questions a future
                         execution phase must answer before it can exist.
  * routing            — see above.

KNOWN LIMITS OF THIS PHASE, STATED RATHER THAN DISCOVERED LATER
---------------------------------------------------------------
  * Daily bars only. A daily-bar signal on a 24/7 asset is a slower instrument
    than the asset deserves; intraday is a later phase with its own data cost.
  * The concentration and daily-loss limits in the risk layer assume
    uncorrelated names and a calendar trading day. Crypto majors are highly
    mutually correlated and have no session boundary, so those limits would be
    wrong for crypto — which does not matter while nothing executes, and must
    be fixed before anything does.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Awaitable, Callable, Optional

from app.core.config import settings
from app.services.crypto_signal_engine import (
    CRYPTO_SCORING_VERSION,
    MIN_BARS,
    normalize_crypto_symbol,
    prices_representable,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Stamped into signal_outcomes.regime (String(30)) for every crypto row. An
# explicit sentinel, not NULL: NULL reads as "we failed to record it", whereas
# this states that no crypto regime classifier exists yet. Whoever builds one
# can find every row generated before it by this exact value.
CRYPTO_REGIME_UNCLASSIFIED = "crypto_unclassified"

# Most recent crypto signals, newest first, for GET /api/crypto/signals.
# In-memory and wiped on restart, exactly like the equity store it mirrors —
# signal_outcomes is the durable record; this is the display buffer.
_recent_crypto_signals: list[dict] = []
_RECENT_CAP = 200

# Fallback notional for the advisory trade plan when the live account value is
# unavailable. Phase 1 submits nothing, so this only scales a displayed
# position_size; a hard failure here would throw away the signal itself, which
# is the part that actually matters.
_FALLBACK_ACCOUNT_VALUE = 100_000.0

BarsFetcher = Callable[[str, int], Awaitable[list]]


async def fetch_crypto_bars(symbol: str, limit: int = 250) -> list:
    """
    Daily OHLCV bars for a crypto symbol via yfinance.

    yfinance serves crypto under the same DASH-form ticker the watchlist stores
    (``BTC-USD``), which is why phase 1 needs no broker data subscription and no
    Alpaca crypto endpoint at all — a deliberate simplification, since the
    v1beta3 crypto endpoint's response shape could not be verified from this
    environment and an unverified parser is a worse dependency than none.

    Two crypto-specific differences from the equity fetcher in main.py:
    ``period`` needs far less padding because crypto prints 7 bars a week rather
    than 5, and there is no ``^VIX`` special case to carry.

    Rows with missing or non-positive OHLC are skipped rather than raising. The
    live in-progress bar routinely arrives with NaN prices, and on a 24/7 asset
    there is *always* an in-progress bar — so this is the normal case here, not
    an edge case.
    """
    import math

    import yfinance as yf

    from app.broker.broker_interface import Bar

    loop = asyncio.get_running_loop()
    ticker = normalize_crypto_symbol(symbol)

    def _fetch() -> list:
        period = f"{min(limit + 15, 730)}d"
        hist = yf.Ticker(ticker).history(period=period, auto_adjust=True)
        if hist.empty:
            return []
        hist = hist.tail(limit)
        bars: list = []
        for ts, row in hist.iterrows():
            try:
                o = float(row["Open"])
                h = float(row["High"])
                lo = float(row["Low"])
                c = float(row["Close"])
            except (TypeError, ValueError, KeyError):
                continue
            if any(math.isnan(x) or x <= 0 for x in (o, h, lo, c)):
                continue
            try:
                bars.append(Bar(
                    timestamp=ts.to_pydatetime(),
                    open=Decimal(str(round(o, 4))),
                    high=Decimal(str(round(h, 4))),
                    low=Decimal(str(round(lo, 4))),
                    close=Decimal(str(round(c, 4))),
                    volume=int(row.get("Volume", 0) or 0),
                ))
            except Exception:
                continue
        return bars

    return await loop.run_in_executor(None, _fetch)


async def _account_value() -> float:
    """Live account value for sizing, or the fallback notional."""
    try:
        from app.services.account_state import get_account_value

        value = await get_account_value()
        if value and float(value) > 0:
            return float(value)
    except Exception as exc:
        logger.warning("Crypto scan: account value unavailable (%s) — using fallback", exc)
    return _FALLBACK_ACCOUNT_VALUE


async def run_crypto_scan(bars_fetcher: Optional[BarsFetcher] = None) -> dict:
    """
    Scan the crypto watchlist, record routable signals, return a summary.

    Never raises: this runs on the background scheduler alongside the equity and
    options scans, and a crypto data hiccup must not take those down with it.

    ``bars_fetcher`` is injectable so tests can drive the whole pipeline —
    indicators, scoring, guards, recording — without a network call. Production
    passes nothing and gets fetch_crypto_bars.
    """
    summary = {
        "enabled": bool(settings.crypto_enabled),
        "scanned": 0, "signals": 0, "routable": 0, "recorded": 0,
        "skipped_insufficient_bars": 0, "skipped_unrepresentable_price": 0,
        "errors": 0,
    }
    if not settings.crypto_enabled:
        return summary

    fetch = bars_fetcher or fetch_crypto_bars
    watchlist = settings.get_crypto_watchlist()
    if not watchlist:
        return summary

    import pandas as pd

    from app.services.equity_signal_engine import (
        compute_equity_trade_plan,
        compute_indicators,
        score_equity_signal,
    )
    from app.services.opportunity_score import compute_opportunity_score

    logger.info("Crypto scan starting — %d symbols (read-only, no execution)", len(watchlist))
    account_value = await _account_value()
    semaphore = asyncio.Semaphore(max(1, settings.crypto_scan_concurrency))

    async def _scan_one(symbol: str) -> Optional[dict]:
        async with semaphore:
            try:
                bars = await fetch(symbol, 250)
                summary["scanned"] += 1
                if len(bars) < MIN_BARS:
                    summary["skipped_insufficient_bars"] += 1
                    return None

                df = pd.DataFrame([{
                    "open": float(b.open), "high": float(b.high),
                    "low": float(b.low), "close": float(b.close), "volume": b.volume,
                } for b in bars])
                ind = compute_indicators(df)
                if not ind:
                    return None

                # orderflow_score omitted, not zeroed by hand: 0.0 is the
                # function's own neutral default. See the module docstring.
                action, confidence, reasons = score_equity_signal(ind)

                routable = action in ("BUY", "SELL") and confidence >= settings.crypto_min_confidence

                trade_plan: dict = {}
                if routable:
                    trade_plan = compute_equity_trade_plan(
                        ind, action, portfolio_value=account_value,
                        max_position_pct=settings.crypto_max_position_pct,
                    )
                    # The guard that keeps a Numeric(12, 4) column from
                    # recording a geometry the signal does not have. A symbol
                    # whose prices collapse is downgraded to non-routable
                    # rather than dropped, so it still appears in the display
                    # feed and the reason is visible instead of the signal
                    # silently vanishing.
                    if not prices_representable(
                        trade_plan.get("entry_price"),
                        trade_plan.get("stop_price"),
                        trade_plan.get("target_price"),
                    ):
                        logger.warning(
                            "Crypto scan: %s trade plan not representable at "
                            "Numeric(12,4) — not recording", symbol,
                        )
                        summary["skipped_unrepresentable_price"] += 1
                        routable = False
                        reasons = {**reasons, "price_precision_guard": "prices not representable"}

                signal = {
                    "id":               str(uuid.uuid4()),
                    "ticker":           symbol,
                    "asset_type":       "crypto",
                    "source":           "Crypto Signal Scanner",
                    "generated_at":     datetime.now(timezone.utc).isoformat(),
                    "action":           action,
                    "confidence":       round(confidence, 4),
                    "signal_score":     round(confidence, 4),
                    # Stated, not implied: no crypto orderflow feed exists.
                    "orderflow_score":  0.0,
                    "iv_overlay_boost": 0.0,
                    "earnings_gated":   False,
                    "reasons":          reasons,
                    "trade_plan":       trade_plan,
                    "routable":         routable,
                    "regime":           CRYPTO_REGIME_UNCLASSIFIED,
                    # Explicit neutral so the ranking factor is a decision.
                    # Without it _regime_score() would also return 0.5, but by
                    # failing a RegimeType lookup — the right number for the
                    # wrong reason, and silently wrong the day a crypto regime
                    # classifier lands.
                    "regime_score":     0.5,
                    "signal_engine_version": CRYPTO_SCORING_VERSION,
                    "indicators": {
                        "rsi":          ind.get("rsi"),
                        "macd":         ind.get("macd"),
                        "bb_pct_b":     ind.get("bb_pct_b"),
                        "atr":          ind.get("atr"),
                        "volume_ratio": ind.get("volume_ratio"),
                    },
                }
                signal["opportunity_score"] = compute_opportunity_score(signal)
                logger.info("Crypto scan: %s → %s (conf=%.2f)", symbol, action, confidence)

                if routable:
                    from app.services.signal_outcome_tracker import record_signal

                    row_id = await record_signal(signal)
                    if row_id:
                        signal["outcome_id"] = row_id
                        summary["recorded"] += 1
                return signal
            except Exception as exc:
                summary["errors"] += 1
                logger.warning("Crypto scan failed for %s: %s", symbol, exc)
                return None

    results = await asyncio.gather(*[_scan_one(s) for s in watchlist])

    for signal in results:
        if signal is None:
            continue
        summary["signals"] += 1
        if signal["routable"]:
            summary["routable"] += 1
        _recent_crypto_signals.insert(0, signal)
    del _recent_crypto_signals[_RECENT_CAP:]

    # NO ROUTING BLOCK HERE, and its absence is the point of phase 1. The equity
    # scan ranks its routable signals and hands them to handle_signal; this
    # function stops at the recorder.
    logger.info(
        "Crypto scan done — %d signals, %d routable, %d recorded, %d errors",
        summary["signals"], summary["routable"], summary["recorded"], summary["errors"],
    )
    return summary


def recent_crypto_signals(limit: int = 50) -> list[dict]:
    """Newest-first slice of the in-memory display feed."""
    return _recent_crypto_signals[:max(0, limit)]
