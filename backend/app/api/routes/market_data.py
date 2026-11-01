"""Market data routes."""
import asyncio
from datetime import datetime

from fastapi import APIRouter

from app.broker.broker_factory import get_broker
from app.broker.ibkr_coordinator import Priority, ibkr_coordinator
from app.core.config import settings
from app.services import options_chain_cache

# yfinance is used for all price display (ticker strip, snapshots).
# IBKR market data requires a paid subscription — we use IBKR only for
# order execution, account data, and options chain data.

router = APIRouter()


async def _yfinance_snapshot(symbol: str) -> dict:
    """
    Fallback price snapshot using yfinance (free, no subscription needed).
    Returns last close + previous close for change_pct calculation.
    Runs in a thread pool to avoid blocking the asyncio event loop.
    """
    import yfinance as yf
    loop = asyncio.get_running_loop()

    def _fetch():
        ticker = yf.Ticker(symbol)
        # Intraday 1-min bars for current price during market hours
        intra = ticker.history(period="1d", interval="1m", auto_adjust=True)
        # Daily bars for prev_close reference
        daily = ticker.history(period="5d", auto_adjust=True)
        if daily.empty:
            return {}
        daily_closes = daily["Close"].dropna()
        if len(daily_closes) < 1:
            return {}
        # Current price: last intraday bar if available, else last daily close
        if not intra.empty:
            last = float(intra["Close"].dropna().iloc[-1])
        else:
            last = float(daily_closes.iloc[-1])
        # prev_close = the previous COMPLETED session's close, i.e. the reference
        # for today's % change.
        #
        # NOTE: yfinance's daily history includes TODAY's in-progress bar as the
        # last row during/after market open. So `daily_closes.iloc[-1]` is today's
        # (current) price — using it as prev_close made change_pct ~= 0 and the
        # ticker always render green. The correct reference is iloc[-2] whenever a
        # prior session exists, regardless of whether the market is open.
        if len(daily_closes) >= 2:
            prev_close = float(daily_closes.iloc[-2])
        else:
            prev_close = float(daily_closes.iloc[-1])
        chg_pct    = round((last - prev_close) / prev_close * 100, 2) if prev_close else 0.0
        return {
            "symbol":     symbol,
            "bid":        None,
            "ask":        None,
            "mid":        last,
            "last_close": last,
            "prev_close": prev_close,
            "change_pct": chg_pct,
            "spread":     None,
            "timestamp":  datetime.utcnow().isoformat(),
            "source":     "yfinance",
        }

    return await loop.run_in_executor(None, _fetch)


@router.get("/snapshot/{symbol}")
async def get_snapshot(symbol: str):
    """
    Return latest price snapshot for a symbol via yfinance.

    IBKR is used only for order execution and account data — not for
    price display, which requires a paid market data subscription.
    yfinance gives free end-of-day prices (and intraday when market is open)
    with no subscription needed.
    """
    try:
        data = await _yfinance_snapshot(symbol)
        if data:
            return data
        return {"symbol": symbol, "error": "No data returned"}
    except Exception as exc:
        return {"symbol": symbol, "error": str(exc)}


@router.get("/sector-rotation")
async def get_sector_rotation():
    """11 GICS sector ETFs ranked by trailing return (1D/1W/1M/3M), with a
    rank-change indicator derived from the same bars fetch — see
    sector_rotation_engine.py for the ranking design."""
    from app.services import sector_rotation_engine
    try:
        return await sector_rotation_engine.get_sector_rotation()
    except Exception as exc:
        return {"error": str(exc), "sectors": [], "excluded": []}


@router.get("/options-chain/{symbol}")
async def get_options_chain(symbol: str, expiry: str = ""):
    """Fetch live options chain from IBKR for the given symbol and expiry.

    Routed through the IBKR request coordinator at interactive (P1)
    priority — this used to queue for minutes behind background scan
    traffic on the shared IBKR connection since nothing prioritized it.
    Also routed through a short-lived cache (options_chain_cache.py) so
    duplicate/rapid requests for the same chain don't each cost a fresh
    IBKR round-trip. The response's data_status (LIVE/DEGRADED/STALE)
    tells the caller exactly how fresh what they got is — never silently
    presented as live when it isn't.
    """
    if not expiry:
        # Default to nearest monthly expiry (~30 DTE)
        from datetime import date, timedelta
        today = date.today()
        # Find next third Friday
        d = today + timedelta(days=30)
        while d.weekday() != 4:  # Friday
            d += timedelta(days=1)
        expiry = d.strftime("%Y-%m-%d")

    async def _serialize() -> dict:
        broker = get_broker()
        chain  = await broker.get_options_chain(symbol, expiry)
        return {
            "symbol":           symbol,
            "expiry":           expiry,
            "underlying_price": float(chain.underlying_price),
            "fetched_at":       chain.fetched_at.isoformat(),
            "calls": [
                {
                    "strike":        float(c.strike),
                    "bid":           float(c.bid),
                    "ask":           float(c.ask),
                    "last":          float(c.last),
                    "volume":        c.volume,
                    "open_interest": c.open_interest,
                    "delta":         c.greeks.delta if c.greeks else None,
                    "gamma":         c.greeks.gamma if c.greeks else None,
                    "theta":         c.greeks.theta if c.greeks else None,
                    "vega":          c.greeks.vega if c.greeks else None,
                    "iv":            c.greeks.implied_vol if c.greeks else None,
                }
                for c in chain.calls
            ],
            "puts": [
                {
                    "strike":        float(p.strike),
                    "bid":           float(p.bid),
                    "ask":           float(p.ask),
                    "last":          float(p.last),
                    "volume":        p.volume,
                    "open_interest": p.open_interest,
                    "delta":         p.greeks.delta if p.greeks else None,
                    "gamma":         p.greeks.gamma if p.greeks else None,
                    "theta":         p.greeks.theta if p.greeks else None,
                    "vega":          p.greeks.vega if p.greeks else None,
                    "iv":            p.greeks.implied_vol if p.greeks else None,
                }
                for p in chain.puts
            ],
        }

    async def _fetch() -> dict:
        return await ibkr_coordinator.submit(
            Priority.P1, _serialize,
            key=f"chain:{symbol.upper()}:{expiry}",
            timeout=120.0, req_type="OPTION_CHAIN", symbol=symbol,
        )

    try:
        chain_dict, data_status = await options_chain_cache.get_chain(symbol, expiry, _fetch)
        return {**chain_dict, "data_status": data_status}
    except Exception as exc:
        return {"symbol": symbol, "expiry": expiry, "error": str(exc)}


@router.get("/iv-rank/{symbol}")
async def get_iv_rank(symbol: str):
    """
    Compute IV rank for a symbol using 60 days of daily bar ATR as a vol proxy.
    True IV rank requires options chain history; this is a fast approximation.
    """
    try:
        import yfinance as yf, asyncio
        loop = asyncio.get_running_loop()

        def _fetch():
            hist = yf.Ticker(symbol).history(period="1y", auto_adjust=True)
            return hist

        hist = await loop.run_in_executor(None, _fetch)
        if hist.empty or len(hist) < 30:
            return {"symbol": symbol, "iv_rank": None, "error": "insufficient data"}

        import pandas as pd, numpy as np
        close = hist["Close"].reset_index(drop=True)
        high  = hist["High"].reset_index(drop=True)
        low   = hist["Low"].reset_index(drop=True)

        # ATR-based realized vol as IV proxy
        tr   = pd.concat([high - low,
                          (high - close.shift()).abs(),
                          (low  - close.shift()).abs()], axis=1).max(axis=1)
        atr  = tr.rolling(14).mean()
        rv   = (atr / close * np.sqrt(252) * 100)   # annualised % vol

        rv_now  = float(rv.iloc[-1])
        rv_high = float(rv.max())
        rv_low  = float(rv.min())
        iv_rank = round((rv_now - rv_low) / max(rv_high - rv_low, 0.01) * 100, 1)

        # Use regime VIX if available for context
        from app.main import _current_regime
        vix = None
        if _current_regime and _current_regime.features_used:
            vix = _current_regime.features_used.vix

        return {
            "symbol":       symbol,
            "iv_rank":      iv_rank,
            "iv_now_pct":   round(rv_now, 1),
            "iv_high_pct":  round(rv_high, 1),
            "iv_low_pct":   round(rv_low, 1),
            "vix":          vix,
            "method":       "atr_rv_proxy",
        }
    except Exception as exc:
        return {"symbol": symbol, "iv_rank": None, "error": str(exc)}


@router.get("/status")
async def get_market_status():
    """
    Whether the US market is open right now (RTH, ET, holiday-aware) and whether
    order execution is currently gated by market hours.
    """
    from app.utils.market_hours import market_status
    status = market_status()
    status["execution_gated"] = bool(getattr(settings, "market_hours_only", True))
    return status


@router.get("/regime")
async def get_regime():
    """Return current regime classification and active strategies."""
    from app.main import _current_regime
    if _current_regime is None:
        return {
            "regime": "unknown",
            "description": "Regime not yet classified",
            "equity_allowed": False,
            "options_allowed": False,
            "equity_strategies": [],
            "options_strategies": [],
            "equity_size_multiplier": 0.5,
            "options_size_multiplier": 0.5,
        }
    vix = None
    iv_rank = None
    if _current_regime.features_used:
        vix     = round(_current_regime.features_used.vix, 2)
        iv_rank = round(_current_regime.features_used.iv_rank, 1)

    return {
        "regime":                  _current_regime.regime.value,
        "description":             _current_regime.description,
        "confidence":              _current_regime.confidence,
        "equity_allowed":          _current_regime.equity_allowed,
        "options_allowed":         _current_regime.options_allowed,
        "equity_strategies":       _current_regime.equity_strategies_allowed,
        "options_strategies":      _current_regime.strategies_allowed,
        "equity_size_multiplier":  _current_regime.equity_size_multiplier,
        "options_size_multiplier": _current_regime.options_size_multiplier,
        "classified_at":           _current_regime.classified_at.isoformat(),
        "vix":                     vix,
        "iv_rank":                 iv_rank,
    }


@router.get("/greeks")
async def get_portfolio_greeks():
    """Return current portfolio-level Greeks snapshot."""
    from app.main import _greeks_tracker
    if _greeks_tracker is None:
        return {"net_delta": 0.0, "net_vega": 0.0, "net_theta": 0.0}
    return _greeks_tracker.snapshot()


@router.get("/broker")
async def get_broker_status():
    """Return active broker name and connection status.

    Connectivity is checked per-broker's own connection model rather than
    IBKR-specific socket introspection alone: IBKR is a persistent
    Gateway/TWS socket that can silently drop (hence the isConnected() check
    below — a hardcoded "connected" lied during a past Gateway outage), while
    a broker like Alpaca is a stateless per-request REST API with no socket
    to drop — it reports connected via its own _connected flag alone.
    """
    try:
        broker = get_broker()
        ib = getattr(broker, "ib", None)
        if ib is not None:
            connected = bool(getattr(broker, "_connected", False)) and bool(ib.isConnected())
        else:
            connected = bool(getattr(broker, "_connected", False))

        if settings.broker.lower() == "alpaca":
            is_paper = "paper" in settings.alpaca_base_url.lower()
        else:
            # Live IBKR ports are 4001 (gateway) / 7496 (TWS). Anything else —
            # including custom container ports like 4004 — is paper, and
            # IBKR_TRADING_MODE is the authoritative override.
            is_paper = settings.ibkr_trading_mode.lower() != "live" \
                       and settings.ibkr_port not in (4001, 7496)

        payload = {
            "broker":           settings.broker,
            "supports_options": broker.supports_options,
            "supports_equities": broker.supports_equities,
            "paper_mode":       is_paper,
            "status":           "connected" if connected else "disconnected",
        }
        if not connected:
            # Say WHY. The UI already renders this field as the ENV chip's
            # tooltip, but only the exception branch below ever set it, so a
            # dropped socket produced a bare "DISCONNECTED" and the reason was
            # reachable only by reading container logs on the host. Config, not
            # credentials: host, port and clientId. No account number, no token.
            payload["error"] = (
                getattr(broker, "_last_connect_error", None)
                or f"No successful connection this process — is TWS/Gateway "
                   f"reachable at {settings.ibkr_host}:{settings.ibkr_port}?"
            )
        return payload
    except Exception as exc:
        return {
            "broker": settings.broker,
            "status": "error",
            "error":  str(exc),
        }
