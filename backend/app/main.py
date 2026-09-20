"""
FastAPI application entry point.
All routes are registered here. Health check at GET /health.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from decimal import Decimal
from datetime import datetime
from typing import Optional

from app.utils.logger import get_logger
from app.utils.request_context import request_id_var


async def _yf_bars(ticker: str, limit: int = 60) -> list:
    """
    Fetch daily OHLCV bars via yfinance (free, no broker subscription needed).
    Returns a list of Bar-like objects compatible with the existing code.
    Used by regime classifier, equity scan, and options scan instead of IBKR.
    IBKR is reserved for order execution and account data only.
    """
    import yfinance as yf
    from app.broker.broker_interface import Bar

    loop = asyncio.get_running_loop()

    def _fetch():
        sym = "^VIX" if ticker.upper() == "VIX" else ticker
        period = f"{min(limit * 2, 730)}d"   # fetch extra to handle weekends/holidays
        hist = yf.Ticker(sym).history(period=period, auto_adjust=True)
        if hist.empty:
            return []
        hist = hist.tail(limit)
        bars = []
        import math as _math
        for ts, row in hist.iterrows():
            # Skip rows with missing/invalid OHLC. yfinance often returns the
            # current in-progress (or after-hours/weekend) bar with NaN prices;
            # building a Bar from it raised 4 validation errors and — unhandled —
            # killed the entire scan. Skip bad rows so the scan stays resilient.
            try:
                o = float(row["Open"]); h = float(row["High"])
                lo = float(row["Low"]); c = float(row["Close"])
            except (TypeError, ValueError):
                continue
            if any(_math.isnan(x) or x <= 0 for x in (o, h, lo, c)):
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

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import (
    analytics,
    backtest,
    journal,
    market_data,
    paper_trade,
    research,
    risk,
    strategy,
    trading_mode,
)
from app.api.routes import equity
from app.api.routes import trade_desk
from app.api.routes import rotation
from app.api.routes import signal_calendar
from app.api.routes import symphony
from app.api.routes import options
from app.api.routes import alpha_edge
from app.api.routes import options_flow
from app.api.routes import income_matrix
from app.api.routes import portfolio
from app.api.routes import options_csp
from app.api.routes import options_decision
from app.api.routes import intel
from app.api.routes import chart
from app.api.routes import alerts
from app.api.routes import ibkr_live
from app.api.routes import forecasts
from app.api.routes import signal_research
from app.core.config import settings

logger = get_logger(__name__)

# Default-deny: the session check is a dependency on the APP, not on individual
# routes. 144 route decorators exist and 15 carried the operator-key dependency;
# adding the rest one at a time guarantees one gets missed, and the missed one is
# found by an incident. Routes opt OUT by path via auth_deps.PUBLIC_*, and
# test_auth_route_coverage.py fails CI if a new route is neither protected nor
# deliberately allowlisted.
#
# Inert while AUTH_ENABLED is false (the default) — require_session returns
# immediately, so existing installs are unchanged.
from app.api.auth_deps import require_session  # noqa: E402

app = FastAPI(
    title="OlbosTrade",
    version="4.0.0",
    description="Blessed prosperity through disciplined, rules-based quantitative trading.",
    dependencies=[Depends(require_session)],
)

_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:3001",
    "http://127.0.0.1:3001",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """
    Threads one ID through every log line a request touches (route handler,
    IBKR coordinator jobs it submits, DB calls) so a single request can be
    traced across the app from logs alone. Echoes a client-supplied
    X-Request-ID when present so an upstream proxy's ID survives.
    """
    req_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    token = request_id_var.set(req_id)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = req_id
    return response

# ── Global singletons (populated at startup) ───────────────────────────────
_current_regime: Optional[object] = None   # RegimeState
_greeks_tracker: Optional[object] = None   # PortfolioGreeksTracker
_signal_scorer: Optional[object] = None    # SignalScorer (lazy — loads model pkl)

# Tracks when a DB-open trade was first seen missing from the broker, so we can
# wait for execution data before booking an exit instead of fabricating $0 P&L.
_close_pending: dict[str, datetime] = {}
CLOSE_GRACE_SECONDS = 300   # wait up to 5 min for a real exit price, then book unknown

# Entry-side reconciliation: when a `pending` trade's order is still working we
# wait this long (measured from trade.entry_date, not process uptime -- a
# restart must never reset this clock) for it to fill. After the window, an
# order with no broker position and no fill execution is treated as
# terminated-unfilled and the pending trade is cancelled (so unfilled/
# ProgramCancelled limit orders never become phantom positions, or worse,
# indefinitely block the duplicate-open guard for that underlying). Generous
# because DAY limit orders can sit unfilled.
FILL_GRACE_SECONDS = 1800   # 30 min for a working order to fill before cancelling

# Heartbeat: monotonic timestamp of the last background-scheduler loop, used by the
# Executive Summary's "Trading Agent — Running" health check.
_scheduler_last_tick: float = 0.0

# Equity scan only ever samples 5 tickers per tick (broker/API rate limits) —
# this rotates which 5, so all watchlist symbols get scanned over several
# cycles instead of the same first-5 forever (previously watchlist[:5] meant
# 8 of 13 charter symbols were never scanned by autopilot at all).
_equity_scan_offset: int = 0

# _reclassify_regime() logs and returns on data failure, silently keeping
# whatever classification is already in _current_regime — with no age check,
# a regime from hours ago (or a permanently-failing reclassify) could gate
# strategy selection indefinitely. 2 hours is 4x the 30-min reclassify
# interval — generous for a transient yfinance outage, not indefinite.
MAX_REGIME_AGE_SECONDS = 2 * 60 * 60

# Hysteresis on regime CHANGES. Created lazily and kept for the process
# lifetime, because the guard is all state — a per-call instance would confirm
# nothing. A restart resets it, which is correct: with no history the first
# reading is adopted immediately rather than being withheld against a regime
# that may no longer apply.
_regime_stabilizer_instance = None


def _regime_stabilizer():
    global _regime_stabilizer_instance
    if _regime_stabilizer_instance is None:
        from app.core.config import settings
        from app.services.regime_stabilizer import RegimeStabilizer
        _regime_stabilizer_instance = RegimeStabilizer(
            required=settings.regime_confirm_readings
        )
    return _regime_stabilizer_instance

# Signal-outcome resolution budget. These rows are the training labels for any
# future model that learns from signal outcomes, so a pass that cannot finish
# must end cleanly and say so — never be cancelled mid-write, which is what
# silently produced a label set covering only 32 of 102 tickers.
# The job stops itself at DEADLINE; GUARD is a backstop for a real hang and is
# deliberately larger, so the guard can no longer be the effective limit.
SIGNAL_OUTCOMES_DEADLINE_S = 240.0
SIGNAL_OUTCOMES_GUARD_S = 600

# ── Route registration ─────────────────────────────────────────────────────
from app.api.routes import auth as auth_routes  # noqa: E402
app.include_router(auth_routes.router)

# Two routers, on two prefixes. The public one is allowlisted in auth_deps;
# the operator one must not be, and lives under /api/admin/ so that it cannot
# be reached by a path-based allowlist entry. See the module docstring.
from app.api.routes import access_requests as access_request_routes  # noqa: E402
app.include_router(access_request_routes.router)
app.include_router(access_request_routes.admin_router)

app.include_router(backtest.router,    prefix="/api/backtest",    tags=["Backtest"])
app.include_router(strategy.router,    prefix="/api/strategy",    tags=["Strategy"])
app.include_router(alpha_edge.router,  prefix="/api/alpha-edge",  tags=["Alpha Edge"])
app.include_router(paper_trade.router, prefix="/api/paper-trade", tags=["Paper Trade"])
app.include_router(market_data.router, prefix="/api/market",      tags=["Market Data"])
app.include_router(risk.router,        prefix="/api/risk",         tags=["Risk"])
app.include_router(options_flow.router, prefix="/api/options-flow", tags=["Options Flow"])
app.include_router(income_matrix.router, prefix="/api/income-matrix", tags=["Income Matrix"])
app.include_router(research.router,    prefix="/api/research",     tags=["Research"])
app.include_router(journal.router,     prefix="/api/journal",      tags=["Journal"])
app.include_router(analytics.router,   prefix="/api/analytics",    tags=["Analytics"])
app.include_router(trading_mode.router,prefix="/api/mode",         tags=["Trading Mode"])
app.include_router(equity.router,      prefix="/api/equity",       tags=["Equity"])
app.include_router(trade_desk.router,  prefix="/api/trade-desk",   tags=["Trade Desk"])
app.include_router(symphony.router,    prefix="/api/symphony",     tags=["Symphony"])
app.include_router(options.router,     prefix="/api/options",      tags=["Options"])
app.include_router(portfolio.router,   prefix="/api/portfolio",    tags=["Portfolio"])
app.include_router(rotation.router,    prefix="/api/rotation",     tags=["Rotation"])
app.include_router(signal_calendar.router, prefix="/api/signals", tags=["Signals"])
app.include_router(options_csp.router, prefix="/api/options/csp",   tags=["Options Income"])
app.include_router(options_decision.router, prefix="/api/options-decision", tags=["Options Decision"])
app.include_router(intel.router,       prefix="/api/intel",        tags=["Intelligence Hub"])
app.include_router(chart.router,       prefix="/api/chart",        tags=["Chart Intelligence"])
app.include_router(alerts.router,      prefix="/api/alerts",       tags=["Smart Alerts"])
app.include_router(alerts.notif_router,prefix="/api/notifications",tags=["Notifications"])
app.include_router(ibkr_live.router,   prefix="/api/ibkr",         tags=["IBKR Live Data"])
app.include_router(forecasts.router,   prefix="/api/forecasts",    tags=["Probabilistic Intelligence"])
app.include_router(signal_research.router, prefix="/api/signal-research", tags=["Signal Research"])


# ── Startup ─────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def on_startup() -> None:
    global _current_regime, _greeks_tracker

    # 1. Initialize broker and connect
    try:
        from app.broker.broker_factory import get_broker
        broker = get_broker()
        logger.info(
            "Broker initialized: %s | options=%s equities=%s",
            settings.broker, broker.supports_options, broker.supports_equities,
        )
        # IBKR requires an explicit async connect; Alpaca is stateless REST
        if hasattr(broker, "connect"):
            try:
                await broker.connect()
                logger.info("Broker connected successfully")
            except Exception as conn_exc:
                logger.warning(
                    "Broker connect() failed (will retry on first use): %s", conn_exc
                )

        # Wire kill switch — must happen after broker is available
        from app.services.kill_switch import kill_switch_service
        kill_switch_service.configure(broker)
        # Restore engaged state from DB so a restart can't silently re-enable
        # trading after the kill switch was engaged.
        await kill_switch_service.rehydrate()
        logger.info("Kill switch wired to broker (engaged=%s)", kill_switch_service.is_engaged)

        # Restore execution mode (manual/copilot/autopilot) from the DB so a
        # restart can't silently reset an operator's chosen mode back to default.
        from app.services.execution_mode import execution_mode_manager
        await execution_mode_manager.rehydrate()

        # Restore trading mode (conservative/balanced/aggressive/scalper) from
        # the DB — same rationale as execution mode above. Previously read from
        # a container-local /tmp file, wiped on every deploy.
        from app.services.trading_mode import trading_mode_manager
        await trading_mode_manager.rehydrate()

        # Account mode guard — log loudly at startup if the gateway's real account
        # doesn't match IBKR_TRADING_MODE (e.g. configured paper but logged into a
        # live account). Execution is independently fail-closed per order; this is
        # the early-warning surface.
        try:
            from app.services.account_guard import verify_account_mode
            ok, detail = await verify_account_mode(broker)
            if ok:
                logger.info("Account mode verified — %s", detail)
            else:
                logger.critical("ACCOUNT MODE GUARD: %s — execution will be blocked", detail)
        except Exception as ag_exc:
            logger.warning("Account mode verification skipped: %s", ag_exc)
    except Exception as exc:
        logger.warning("Broker initialization failed (non-fatal): %s", exc)

    # 2. Initialize portfolio Greeks tracker
    from app.services.portfolio_greeks import PortfolioGreeksTracker
    _greeks_tracker = PortfolioGreeksTracker()
    logger.info("PortfolioGreeksTracker initialized")

    # 3. Classify regime immediately, then run equity scan
    async def _startup_market_init():
        await _reclassify_regime()
        await _run_equity_scan()
    asyncio.create_task(_startup_market_init())

    if settings.execution_test_mode:
        logger.warning(
            "⚠ EXECUTION_TEST_MODE is ON — quality/frequency guards bypassed; the "
            "system will trade freely (PAPER validation). Disable for real runs."
        )

    # 4. Start background scheduler
    asyncio.create_task(_background_scheduler())

    # 4b. Intelligence Hub — register free news/filing/macro providers + seed
    # default smart watchlists (idempotent, non-fatal).
    try:
        from app.services.intel.bootstrap import register_default_providers
        from app.services.intel.watchlist_service import seed_defaults
        register_default_providers()
        await seed_defaults()
    except Exception as exc:
        logger.warning("Intel init skipped (non-fatal): %s", exc)

    # Initialize IBKR Live Data WebSocket broker (real-time quotes for scan panels)
    try:
        await ibkr_live.startup_ibkr_live()
    except Exception as exc:
        logger.warning("IBKR Live data broker init skipped (non-fatal): %s", exc)


@app.on_event("shutdown")
async def on_shutdown() -> None:
    """Cleanup the IBKR Live Data broker on shutdown."""
    try:
        await ibkr_live.shutdown_ibkr_live()
    except Exception as exc:
        logger.warning("IBKR Live data broker shutdown failed: %s", exc)


async def _guarded(coro, name: str, timeout: float) -> None:
    """
    Run a scheduler sub-task with a hard timeout. A hung broker/IO call (e.g. an
    IBKR request that never returns) must never freeze the whole scheduler — that
    stops the heartbeat and the Trading Agent shows "Not ticking". On timeout or
    error we log and move on; the loop keeps ticking.
    """
    global _scheduler_last_tick
    import time as _t
    token = request_id_var.set(f"sched-{name}-{uuid.uuid4().hex[:8]}")
    try:
        await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        logger.error("Scheduler task '%s' timed out after %.0fs — skipped", name, timeout)
    except Exception as exc:
        logger.error("Scheduler task '%s' failed: %s", name, exc)
    finally:
        # Refresh the heartbeat after each sub-task so a legitimately slow scan
        # can't make the agent look stalled.
        _scheduler_last_tick = _t.monotonic()
        request_id_var.reset(token)


async def _background_scheduler() -> None:
    """Background task that runs periodic scans and updates."""
    global _current_regime, _scheduler_last_tick

    equity_interval_s  = settings.equity_signal_interval_minutes * 60
    options_interval_s = 30 * 60   # 30 minutes
    regime_interval_s  = 30 * 60   # 30 minutes
    greeks_interval_s  = 60        # 1 minute
    fills_interval_s   = 30        # 30 seconds
    # Daily bars only update once/trading day — checking more often than a
    # few times a day just burns yfinance calls without new information.
    signal_outcomes_interval_s = 6 * 60 * 60   # 6 hours
    # The job owns an inner deadline (SIGNAL_OUTCOMES_DEADLINE_S) and stops at
    # a ticker boundary; the guard here is only a backstop for a genuine hang,
    # so it sits above that deadline rather than cutting the pass short. At 120s
    # the guard WAS the limit, and it killed every run mid-write.
    reconciliation_interval_s  = 5 * 60        # 5 minutes
    # Daily-bar-derived like regime/options, but not gated on daily-close
    # semantics — keeps position_rotation.py's correlation tiebreaker
    # reasonably fresh without a live yfinance fetch in the money path.
    correlation_interval_s     = 20 * 60       # 20 minutes
    # Sector membership is static company data — a daily resolution is
    # generous. 100 yfinance lookups, off the money path, capped at 5 at once.
    sector_interval_s          = 24 * 60 * 60  # daily
    # Polled often, gated internally to the 10:00 ET hour and one write per
    # date. A restart at 10:05 still catches the day; a once-daily timer
    # would not.
    snapshot_interval_s        = 10 * 60       # 10 minutes

    import time as _time
    _now = _time.monotonic()
    # Start timers at "now" so the scheduler doesn't fire immediately
    # (startup already ran regime + equity scan)
    last_equity  = _now
    last_options = _now
    last_regime  = _now
    last_greeks  = 0.0   # Greeks update on first tick is fine (lightweight)
    last_fills   = 0.0
    last_signal_outcomes = _now
    last_reconciliation  = 0.0   # cheap local-cache read — fine to run on first tick
    # No startup counterpart (unlike equity/options/regime) and a stale/
    # empty cache already fails open safely — fine to run on first tick.
    last_correlation     = 0.0
    # Fire on the first tick: until this resolves, the sector cap sees almost
    # nothing, so getting it populated early matters more than staggering it.
    last_sector          = 0.0
    # Fire on the first tick: if the process restarted inside the 10:00 ET
    # hour, the day's snapshot is still catchable.
    last_snapshot        = 0.0

    import time

    reconnect_interval_s = 60   # Check broker connection every 60s
    last_reconnect = 0.0

    while True:
        try:
            now = time.monotonic()
            _scheduler_last_tick = now   # heartbeat for the System Health panel
            from app.services.observability import observability as _obs
            _obs.incr("scanner.tick")
            _obs.gauge("scanner.last_tick_monotonic", now)

            # Every 60s: ensure broker is still connected (auto-reconnect).
            # Guarded by the coordinator's reconnect_lock so this can't
            # overlap with another in-flight reconnect attempt triggered
            # elsewhere (e.g. a request handler hitting a disconnect error)
            # and spawn a duplicate connection.
            if now - last_reconnect >= reconnect_interval_s:
                try:
                    from app.broker.broker_factory import get_broker
                    from app.broker.ibkr_coordinator import ibkr_coordinator
                    _broker = get_broker()
                    async with ibkr_coordinator.reconnect_lock:
                        is_connected = getattr(_broker, "_connected", False)
                        if hasattr(_broker, "ib"):
                            is_connected = is_connected and _broker.ib.isConnected()
                        if not is_connected and hasattr(_broker, "connect"):
                            logger.warning("Broker disconnected — attempting reconnect")
                            await asyncio.wait_for(_broker.connect(), timeout=30)
                            logger.info("Broker reconnected successfully")
                except Exception as _rc_exc:
                    logger.warning("Broker reconnect failed: %s", _rc_exc)
                last_reconnect = now

            # Every 30 min: regime reclassify
            if now - last_regime >= regime_interval_s:
                await _guarded(_reclassify_regime(), "regime", 45)
                last_regime = now

            # Missing or stale regime → reset to UNKNOWN (fail-open, reduced
            # size) before either scan branch gates on it, so both branches
            # see the same policy for "we don't actually know the regime".
            _ensure_fresh_regime()

            # Every 15 min: equity signal scan.
            # Run when regime allows equities, OR when regime is not yet classified
            # (treated as UNKNOWN — reduced size, but signals are still generated).
            # Only skip when regime is explicitly CRISIS.
            if now - last_equity >= equity_interval_s:
                regime_blocks_equity = (
                    _current_regime is not None
                    and not getattr(_current_regime, "equity_allowed", True)
                )
                if not regime_blocks_equity:
                    await _guarded(_run_equity_scan(), "equity_scan", 90)
                else:
                    logger.info(
                        "Equity scan skipped — regime %s does not allow equities",
                        getattr(_current_regime, "regime_type", "unknown"),
                    )
                last_equity = now

            # Every 30 min: options spread signal scan across the whole
            # watchlist (if regime allows) — see _run_options_scan_watchlist's
            # docstring for why every symbol reuses SPY's regime as a shared
            # proxy, and for the rank-then-execute-best-first ordering. A
            # generous timeout since this now scans the full watchlist
            # sequentially, not just 2 symbols.
            if now - last_options >= options_interval_s:
                await _guarded(_run_options_scan_watchlist(), "options_scan", 240)
                last_options = now

            # Every 30 sec: poll fills from active broker
            if now - last_fills >= fills_interval_s:
                await _guarded(_poll_fills(), "poll_fills", 20)
                last_fills = now

            # Every 1 min: update portfolio Greeks
            if now - last_greeks >= greeks_interval_s:
                await _guarded(_update_portfolio_greeks(), "greeks", 30)
                last_greeks = now

            # Every 5 min: broker/DB position reconciliation (alert-only —
            # see _reconcile_positions docstring for why this doesn't
            # auto-engage the kill switch)
            if now - last_reconciliation >= reconciliation_interval_s:
                await _guarded(_reconcile_positions(), "reconciliation", 30)
                last_reconciliation = now

                # Immediately behind it, on the same tick: reconciliation is
                # what adopts a position without a stop, so this is the
                # cleanup that belongs directly after it rather than on a
                # timer of its own drifting in and out of phase. Its first
                # step is a DB read that returns early when nothing is
                # missing a stop, so the usual cost is one query.
                await _guarded(_backfill_equity_stops(), "stop_backfill", 45)

                # And its mirror image, on the same tick and for the same
                # reason. The backfill above handles a POSITION WITHOUT AN
                # ORDER; this handles an ORDER WITHOUT A POSITION. Both are
                # created by reconciliation — one adopts, the other closes —
                # so both belong behind it rather than on timers of their own
                # drifting in and out of phase.
                await _guarded(_sweep_orphaned_orders(), "orphan_sweep", 45)

            # Every 20 min: refresh the rotation-scoped correlation cluster
            # cache (position_rotation.py's cluster-membership tiebreaker
            # reads this synchronously — see rotation_correlation_cache.py).
            # Deferred from Phase 3 Increment 1 so rotation never triggers
            # a live yfinance fetch itself.
            if now - last_correlation >= correlation_interval_s:
                await _guarded(_refresh_rotation_correlation_cache(), "rotation_correlation", 60)
                last_correlation = now

            # Daily: resolve ticker -> sector for the watchlist. Static company
            # data, so a slow loop; until it lands the sector cap sees almost
            # nothing, which is why it fires on the first tick.
            if now - last_sector >= sector_interval_s:
                await _guarded(_refresh_sector_cache(), "sector_cache", 300)
                last_sector = now

            # Every 10 min, but the job itself only acts inside the 10:00 ET
            # hour and only once per date — a frequent tick with an internal
            # gate is more robust than one daily alarm that a restart can miss.
            if now - last_snapshot >= snapshot_interval_s:
                await _guarded(_capture_daily_snapshot(), "daily_snapshot", 60)
                last_snapshot = now

            # Every 6 hours: resolve pending signal outcomes against fresh
            # daily bars (hit target, hit stop, or expire past the hold
            # window). See signal_outcome_tracker.py.
            if now - last_signal_outcomes >= signal_outcomes_interval_s:
                await _guarded(_check_signal_outcomes(), "signal_outcomes",
                               SIGNAL_OUTCOMES_GUARD_S)
                last_signal_outcomes = now

        except Exception as exc:
            logger.error("Background scheduler error: %s", exc)

        await asyncio.sleep(10)


async def _reclassify_regime() -> None:
    """
    Classify current market regime using yfinance for SPY + VIX bars.
    Builds a lightweight IVSurfaceSnapshot from VIX price as an ATM IV proxy —
    no live options chain or broker subscription required.
    """
    global _current_regime
    try:
        import pandas as pd
        import numpy as np
        from datetime import datetime, timezone

        # Use yfinance — free, no broker subscription needed
        spy_bars = await _yf_bars("SPY", limit=60)
        vix_bars = await _yf_bars("VIX", limit=60)

        if len(spy_bars) < 15:
            logger.warning("Regime: insufficient SPY bars (%d) — keeping current", len(spy_bars))
            return

        # Build DataFrames
        spy_close = pd.Series([float(b.close) for b in spy_bars])
        spy_high  = pd.Series([float(b.high)  for b in spy_bars])
        spy_low   = pd.Series([float(b.low)   for b in spy_bars])
        returns   = spy_close.pct_change().dropna()

        # RSI(14)
        delta = spy_close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, 1e-9)
        rsi_val = float((100 - 100 / (1 + rs)).iloc[-1])

        # ADX(14) — simplified
        tr    = pd.concat([
            spy_high - spy_low,
            (spy_high - spy_close.shift()).abs(),
            (spy_low  - spy_close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr14 = tr.rolling(14).mean()
        dm_p  = spy_high.diff().clip(lower=0)
        dm_m  = (-spy_low.diff()).clip(lower=0)
        di_p  = 100 * dm_p.rolling(14).mean() / atr14.replace(0, 1)
        di_m  = 100 * dm_m.rolling(14).mean() / atr14.replace(0, 1)
        dx    = 100 * (di_p - di_m).abs() / (di_p + di_m).replace(0, 1)
        adx_val = float(dx.rolling(14).mean().iloc[-1])

        # VIX proxy for IV rank
        if len(vix_bars) >= 5:
            vix_series = pd.Series([float(b.close) for b in vix_bars])
            vix_now    = float(vix_series.iloc[-1])
            vix_high   = float(vix_series.max())
            vix_low    = float(vix_series.min())
            iv_rank    = (vix_now - vix_low) / max(vix_high - vix_low, 0.01) * 100
        else:
            # VIX unavailable — use realized vol as proxy
            rv = float(returns.iloc[-20:].std() * np.sqrt(252)) if len(returns) >= 20 else 0.15
            vix_now = rv * 100
            iv_rank = 30.0  # neutral assumption

        realized_vol = float(returns.iloc[-20:].std() * np.sqrt(252)) if len(returns) >= 20 else 0.15

        # Build minimal IVSurfaceSnapshot (no options chain required)
        from app.services.iv_surface import IVSurfaceSnapshot
        surface = IVSurfaceSnapshot(
            underlying="SPY",
            spot_price=float(spy_close.iloc[-1]),
            fetched_at=datetime.now(timezone.utc),
            iv_rank=iv_rank,
            iv_percentile=iv_rank,
            atm_iv=vix_now / 100.0,
            realized_vol_20d=realized_vol,
            vrp=max(vix_now / 100.0 - realized_vol, 0.0),
            front_skew=None,
            back_skew=None,
            skew_trend=0.0,
            term_structure=None,
            data_quality="partial",
            warnings=["VIX-proxy surface — no live options chain"],
        )

        from app.services.regime_classifier import RegimeClassifier, compute_flow_features
        classifier = RegimeClassifier()
        flow_feats = await compute_flow_features("SPY")
        reading = classifier.classify(
            surface, rsi=rsi_val, adx=adx_val, spy_returns=returns,
            flow_sentiment_score=flow_feats["flow_sentiment_score"],
            flow_large_sweep_bullish_count=flow_feats["flow_large_sweep_bullish_count"],
        )

        # Hysteresis. classify() is stateless and decides on knife-edges of
        # continuous inputs, and this runs every 30 min against a daily bar
        # that is still forming — so the boundary is re-sampled ~13x a session
        # and the regime can flip back and forth within a single day. The
        # stabilizer withholds a CHANGE until consecutive readings agree;
        # CRISIS bypasses it, so risk-off is never delayed.
        stabilized = _regime_stabilizer().observe(reading)
        _current_regime = stabilized.state

        if stabilized.held:
            logger.info(
                "Regime → %s HELD (classifier said %s, %d/%d readings) "
                "(VIX=%.1f, IV_rank=%.0f, RSI=%.1f, ADX=%.1f, equity=%s)",
                _current_regime.regime.value, stabilized.raw.value,
                stabilized.pending_count, stabilized.required,
                vix_now, iv_rank, rsi_val, adx_val, _current_regime.equity_allowed,
            )
        else:
            logger.info(
                "Regime → %s (VIX=%.1f, IV_rank=%.0f, RSI=%.1f, ADX=%.1f, equity=%s)",
                _current_regime.regime.value, vix_now, iv_rank, rsi_val, adx_val,
                _current_regime.equity_allowed,
            )

    except Exception as exc:
        logger.warning("Regime reclassify failed: %s", exc, exc_info=True)


def _ensure_fresh_regime() -> None:
    """
    Reset _current_regime to a synthetic UNKNOWN classification if it's
    missing or older than MAX_REGIME_AGE_SECONDS.

    Previously an unclassified regime (None) was fail-open for equities but
    fail-closed for options (_run_options_scan returns immediately when
    _current_regime is None) — two different policies for the same "we don't
    actually know the regime" state, with no staleness check at all once a
    classification existed. This unifies both paths onto UNKNOWN's own
    fail-open, reduced-size policy (REGIME_CONFIG[RegimeType.UNKNOWN]) —
    CRISIS/kill-switch already cover the fail-closed case for real danger.
    """
    global _current_regime
    from datetime import datetime, timezone
    from app.services.regime_classifier import RegimeState, RegimeType, REGIME_CONFIG

    now = datetime.now(timezone.utc)
    if _current_regime is not None:
        age_s = (now - _current_regime.classified_at).total_seconds()
        if age_s <= MAX_REGIME_AGE_SECONDS:
            return
        logger.warning(
            "Regime classification is stale (%.0fs old, was %s) — "
            "resetting to UNKNOWN until the next successful reclassify",
            age_s, _current_regime.regime.value,
        )
    else:
        logger.info("No regime classification yet — using UNKNOWN until the first reclassify")

    config = REGIME_CONFIG[RegimeType.UNKNOWN]
    _current_regime = RegimeState(
        regime=RegimeType.UNKNOWN,
        description=config["description"],
        confidence=0.0,
        strategies_allowed=config["strategies_allowed"],
        equity_strategies_allowed=config.get("equity_strategies_allowed", []),
        size_multiplier=config["size_multiplier"],
        equity_size_multiplier=config.get("equity_size_multiplier", 0.5),
        options_size_multiplier=config.get("options_size_multiplier", config["size_multiplier"]),
        signal_threshold=config["signal_threshold_override"],
        iron_condor_allowed=config["iron_condor_allowed"],
        credit_spread_allowed=config["credit_spread_allowed"],
        classified_at=now,
        features_used=None,
        reasoning=["regime missing or stale — reset to UNKNOWN by the staleness gate"],
    )


def _rotate_watchlist_window(watchlist: list, offset: int, size: int = 5) -> tuple:
    """
    Pick the next `size` tickers to scan starting at `offset`, wrapping around
    the watchlist, and return (window, next_offset).

    Previously the background scan always took watchlist[:5] — with a 13-symbol
    charter watchlist and a 5-per-tick cap, 8 symbols were never scanned by
    autopilot at all. Rotating means every symbol gets scanned over multiple
    cycles instead of the same first 5 forever.
    """
    if not watchlist:
        return [], offset
    n = len(watchlist)
    start = offset % n
    window = [watchlist[(start + i) % n] for i in range(min(size, n))]
    return window, start + size


def _equity_confluence_reason(
    ticker: str,
    strategy_name: str,
    recent_equity_signals: list,
    now,
    staleness_seconds: int = 3600,
) -> Optional[str]:
    """
    Check whether an options strategy's direction actively conflicts with
    this same ticker's own most recent equity signal. The equity and
    options engines analyze the same stock independently (different
    indicators, different scoring) — one selling calls into a stock the
    equity engine currently reads as strongly bullish (or vice versa) is
    exactly the mismatch worth catching, rather than trusting either read
    in isolation.

    Returns a rejection reason only if the equity engine's most recent
    signal for this ticker is fresh (within staleness_seconds) AND
    actively opposes the strategy's direction. Returns None — no block —
    if there's no equity signal for this ticker yet, it's stale, or it
    doesn't disagree (a HOLD reading isn't a contradiction). This is a
    confirming filter, not a hard prerequisite: missing or stale equity
    data must never itself block options from firing.

    `recent_equity_signals` must be newest-first (matches
    app.api.routes.equity._recent_signals's insert(0, ...) ordering) — the
    first entry matching `ticker` is treated as its most recent signal.
    """
    bullish_strategies = ("bull_put_spread", "bull_call_debit_spread")
    opposing_action = "SELL" if strategy_name in bullish_strategies else "BUY"

    for sig in recent_equity_signals:
        if sig.get("ticker") != ticker:
            continue
        generated_at = sig.get("generated_at")
        try:
            from datetime import datetime as _dt
            age_seconds = (now - _dt.fromisoformat(generated_at)).total_seconds()
        except (TypeError, ValueError):
            return None
        if age_seconds > staleness_seconds:
            return None
        if sig.get("action") == opposing_action:
            return (
                f"conflicts with this ticker's own equity signal "
                f"({opposing_action}, {age_seconds / 60:.0f}min old)"
            )
        return None
    return None


async def _record_options_rejection(
    symbol: str, reason: str, strategy_name: Optional[str] = None, evidence: Optional[dict] = None,
) -> None:
    """
    Record a scanned-but-not-qualified options attempt into the same store
    the Options Signals UI reads (_recent_options_signals) — mirrors how
    the equity scanner already records HOLD signals with a reason, not just
    the ones that qualify.

    Previously every rejection reason (entry conditions failing, a
    confluence conflict, insufficient price history) only ever reached a
    log line — the UI's "0 total" told the user nothing about whether the
    scanner was broken or genuinely found nothing, and why. This makes
    those same reasons visible on the page itself instead of requiring a
    server-log lookup.

    evidence: SHAP top_positive_factors/top_negative_factors from
    SignalScorer.explain() — only the AI-scorer rejection path has this;
    every other rejection reason (insufficient history, zero-sized
    position, etc.) has nothing to attach, so this stays None for them.
    """
    import uuid
    from datetime import datetime, timezone
    from app.api.routes.options import _recent_options_signals

    _recent_options_signals.insert(0, {
        "id":           str(uuid.uuid4()),
        "ticker":       symbol,
        "asset_type":   "options",
        "source":       "Options Signal Scanner",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "action":       "HOLD",
        "confidence":   0.0,
        "reason":       reason,
        "strategy":     strategy_name,
        "evidence":     evidence,
    })
    del _recent_options_signals[200:]

    # Persist alongside the in-memory store. The list above is capped at 200 and
    # dies on restart, which is why "why has nothing fired in a month" has never
    # had data behind it. Best-effort: record_rejection never raises, and a
    # diagnostic write must not break the scan it is diagnosing.
    from app.services.options_rejection_log import record_rejection

    # A rejection profile is only interpretable against which strategies the
    # regime allowed at the time, so stamp it. None when the regime has never
    # classified or has been reset to UNKNOWN — an honest absence.
    regime_name = None
    regime_type = getattr(_current_regime, "regime_type", None)
    if regime_type is not None:
        regime_name = str(getattr(regime_type, "value", regime_type))

    await record_rejection(
        ticker=symbol,
        reason=reason,
        strategy=strategy_name,
        regime=regime_name,
        evidence=evidence,
    )


async def _run_equity_scan() -> None:
    """Background scan — writes results into the same in-memory store as POST /api/equity/scan."""
    global _equity_scan_offset
    try:
        logger.info("Background equity signal scan starting")
        import uuid
        import pandas as pd
        from datetime import datetime, timezone
        from app.broker.broker_factory import get_broker
        from app.services.equity_signal_engine import (
            compute_indicators, compute_equity_trade_plan,
            earnings_gate, score_equity_signal,
        )
        from app.services.orderflow_engine import get_orderflow_score
        from app.services.account_state import get_account_value
        from app.services.opportunity_score import compute_opportunity_score
        from app.api.routes.equity import _recent_signals   # shared in-memory store

        watchlist = settings.get_equity_watchlist()
        # One live account fetch per scan cycle, not per-ticker.
        account_value = await get_account_value()

        # Window size 0 (default) scans the full watchlist every tick — every
        # symbol gets a fresh evaluation each cycle instead of waiting several
        # ticks for rotation to reach it. A smaller explicit size still rotates.
        window_size = settings.equity_scan_window_size or len(watchlist)
        scan_window, _equity_scan_offset = _rotate_watchlist_window(
            watchlist, _equity_scan_offset, size=window_size,
        )

        broker = get_broker()
        # Bounded concurrency: the watchlist grew from 13 to ~59 symbols, and
        # a fully sequential loop (bars fetch + live IBKR quote per ticker)
        # would take minutes, risking overlap with the next scan cycle. A
        # semaphore caps how many tickers are in flight at once rather than
        # firing all of them at IBKR simultaneously (which would risk its
        # market-data pacing limits) or scanning one at a time (too slow).
        semaphore = asyncio.Semaphore(max(1, settings.equity_scan_concurrency))

        async def _scan_one(ticker: str) -> Optional[dict]:
            async with semaphore:
                try:
                    if earnings_gate(ticker, settings.earnings_gate_days):
                        return None
                    # Use yfinance for historical bars — no broker subscription needed.
                    # Need >=200 bars so EMA200 computes; with only 120 it was always
                    # NaN, forcing above_ema200=False and skewing every signal bearish.
                    bars = await _yf_bars(ticker, limit=250)
                    if len(bars) < 30:
                        return None
                    df = pd.DataFrame([{
                        "open": float(b.open), "high": float(b.high),
                        "low": float(b.low),  "close": float(b.close), "volume": b.volume,
                    } for b in bars])
                    ind = compute_indicators(df)
                    if not ind:
                        return None

                    from app.broker.ibkr_coordinator import Priority, ibkr_coordinator
                    orderflow = await ibkr_coordinator.submit(
                        Priority.P2, lambda t=ticker: get_orderflow_score(t, broker),
                        timeout=30.0, req_type="QUOTE", symbol=ticker,
                    )
                    action, confidence, reasons = score_equity_signal(ind, orderflow_score=orderflow)

                    # Test mode routes any BUY/SELL regardless of confidence so the
                    # pipeline actually trades for validation.
                    routable_signal = action in ("BUY", "SELL") and (
                        settings.execution_test_mode
                        or confidence >= settings.effective_equity_min_confidence
                    )

                    trade_plan = {}
                    if routable_signal:
                        trade_plan = compute_equity_trade_plan(
                            ind, action, portfolio_value=account_value,
                        )

                    signal = {
                        "id":              str(uuid.uuid4()),
                        "ticker":          ticker,
                        "asset_type":      "equity",
                        "source":          "Equity Signal Scanner",
                        "generated_at":    datetime.now(timezone.utc).isoformat(),
                        "action":          action,
                        "confidence":      round(confidence, 4),
                        # Equities have no separate POP-derived score — confidence IS
                        # the signal quality. Without this key, record_fill's
                        # signal.get("signal_score", 0) always fell back to 0 and every
                        # equity trade's journal entry showed a blank score.
                        "signal_score":    round(confidence, 4),
                        "orderflow_score": round(orderflow, 4),
                        "iv_overlay_boost": 0.0,
                        "earnings_gated":  False,
                        "reasons":         reasons,
                        "trade_plan":      trade_plan,
                        "routable":        routable_signal,
                        # Same gap as signal_score: without this key, record_fill's
                        # signal.get("regime", "unknown") always fell back to "unknown"
                        # and every equity trade's journal "market context" was useless.
                        "regime": getattr(getattr(_current_regime, "regime", None), "value", None) or "unknown",
                        "indicators": {
                            "rsi":          ind.get("rsi"),
                            "macd":         ind.get("macd"),
                            "bb_pct_b":     ind.get("bb_pct_b"),
                            "atr":          ind.get("atr"),
                            "volume_ratio": ind.get("volume_ratio"),
                        },
                    }
                    signal["opportunity_score"] = compute_opportunity_score(signal)
                    logger.info("Equity scan: %s → %s (conf=%.2f)", ticker, action, confidence)

                    # Wire the alert rule-evaluation engine using metrics already
                    # computed in this loop for free. iv_rank/iv_percentile/vix
                    # need options/vol-surface data this scan doesn't fetch and
                    # stay unwired — separate, larger, explicitly deferred scope.
                    # alpha_edge_entry_score/alpha_edge_risk_score reuse the same
                    # pure-math functions the Alpha Edge equity orchestrator (see
                    # alpha_edge_engine.py) itself calls — not a re-fetch of its
                    # full I/O-bound flow, which would re-hit yfinance/DB per
                    # ticker on every scan cycle.
                    try:
                        prev_close = float(df["close"].iloc[-2]) if len(df) >= 2 else 0.0
                        change_pct = (
                            (float(df["close"].iloc[-1]) - prev_close) / prev_close * 100
                            if prev_close else 0.0
                        )
                        from app.services.alpha_edge_engine import compute_equity_scores as _ae_equity_scores
                        from app.services.trade_frequency_controller import risk_score as _ae_risk_score
                        _alpha_entry, _, _ = _ae_equity_scores(action, confidence, position_direction=None)
                        alert_snapshot = {
                            "price":      ind["close"],                  # ind's key is "close" — map explicitly
                            "rsi_14":     ind["rsi"],                    # ind's key is "rsi" — map explicitly
                            "change_pct": change_pct,                    # not in compute_indicators; computed here
                            "volume":     float(df["volume"].iloc[-1]),  # raw latest volume, NOT volume_ratio
                            "alpha_edge_entry_score": _alpha_entry,
                            "alpha_edge_risk_score":  _ae_risk_score(signal),
                            "opportunity_score":      signal["opportunity_score"]["score"],
                        }
                        from app.services.alerts.service import evaluate_symbol
                        await evaluate_symbol(ticker, alert_snapshot)
                    except Exception as exc:
                        logger.warning("Alert evaluation failed for %s: %s", ticker, exc)
                    if routable_signal:
                        # Track every routable signal's forward outcome, not
                        # just the handful that become actual trades — trades
                        # alone are too few and selection-biased toward
                        # whatever already passed the confidence filter.
                        from app.services.signal_outcome_tracker import record_signal
                        await record_signal(signal)
                    return signal
                except Exception as exc:
                    logger.warning("Equity scan failed for %s: %s", ticker, exc)
                    return None

        results = await asyncio.gather(*[_scan_one(t) for t in scan_window])

        routable: list = []   # qualifying signals to rank + route highest-first
        for signal in results:
            if signal is None:
                continue
            _recent_signals.insert(0, signal)
            # Collect actionable signals; rank + route after the loop so the
            # highest-quality opportunities reach the frequency controller first.
            if signal["routable"]:
                routable.append(signal)

        # Rank by weighted quality score, then route highest-first. The frequency
        # controller inside handle_signal enforces the per-mode daily cap, so once
        # capacity is reached the lower-ranked signals are blocked, not the best.
        if routable:
            from app.services.trade_frequency_controller import trade_frequency_controller
            from app.api.routes.trade_desk import handle_signal
            for sig in trade_frequency_controller.rank(routable):
                try:
                    await handle_signal(sig)
                except Exception as exec_exc:
                    logger.warning("Execution handler failed for %s: %s",
                                   sig.get("ticker"), exec_exc)

        del _recent_signals[200:]   # keep last 200 only

    except Exception as exc:
        logger.warning("Background equity scan failed: %s", exc)


async def _live_spread_quote(
    broker, symbol: str, expiry_iso: str,
    short_strike: float, long_strike: float, opt_type: str,
) -> Optional[dict]:
    """
    Price a vertical spread off the LIVE options chain (real NBBO mids).

    Returns a dict with per-contract ``net_credit`` (dollars), the actual
    available strikes nearest the requested ones, and the short-leg delta — or
    ``None`` when a live chain / valid quotes are not available (e.g. no IBKR
    options market-data subscription), in which case the caller falls back to the
    Black-Scholes estimate.

    Using the real chain means the submitted limit price tracks where the spread
    can actually fill, instead of a theoretical Black-Scholes mid.
    """
    try:
        chain = await broker.get_options_chain(symbol, expiry_iso)
    except Exception as exc:
        logger.debug("Live options chain unavailable for %s %s: %s", symbol, expiry_iso, exc)
        return None

    legs = chain.puts if opt_type == "put" else chain.calls
    if not legs:
        return None

    def _nearest(target: float):
        return min(legs, key=lambda c: abs(float(c.strike) - target))

    short_c = _nearest(short_strike)
    long_c = _nearest(long_strike)
    if float(short_c.strike) == float(long_c.strike):
        return None  # chain too sparse to form the spread

    def _mid(c) -> Optional[float]:
        bid = float(c.bid or 0)
        ask = float(c.ask or 0)
        if bid > 0 and ask > 0 and ask >= bid:
            return (bid + ask) / 2.0
        last = float(getattr(c, "last", 0) or 0)
        return last if last > 0 else None

    short_mid = _mid(short_c)
    long_mid = _mid(long_c)
    if short_mid is None or long_mid is None:
        return None

    net_credit_ps = short_mid - long_mid
    if net_credit_ps <= 0:
        return None  # not a credit at these strikes — let BS / strike selection handle it

    short_delta = abs(float(short_c.greeks.delta)) if short_c.greeks and short_c.greeks.delta else None
    short_gamma = float(short_c.greeks.gamma) if short_c.greeks and short_c.greeks.gamma is not None else None
    return {
        "net_credit":   round(net_credit_ps * 100, 2),
        "short_delta":  short_delta,
        "short_strike": float(short_c.strike),
        "long_strike":  float(long_c.strike),
        # Real per-leg liquidity/gamma data — already fetched above, just not
        # previously surfaced. Consumed by the liquidity/gamma gates in
        # _execute_signal(); the yfinance/Black-Scholes fallback quote paths
        # (below) have no equivalent and must not fabricate these.
        "short_bid":            float(short_c.bid),
        "short_ask":            float(short_c.ask),
        "long_bid":             float(long_c.bid),
        "long_ask":             float(long_c.ask),
        "short_open_interest":  int(short_c.open_interest),
        "long_open_interest":   int(long_c.open_interest),
        "short_gamma":          short_gamma,
    }


async def _yf_options_quote(
    symbol: str, target_expiry_iso: str,
    short_strike: float, long_strike: float, opt_type: str,
) -> Optional[dict]:
    """
    Price a vertical spread off yfinance's (free, ~15-min delayed) option chain.

    Used as the no-cost fallback when the broker chain has no quotes (IBKR without
    an options market-data subscription). Snaps to the nearest listed expiration and
    strikes, and prices each leg at its bid/ask mid (last price if bid/ask missing).
    yfinance does not provide greeks, so short_delta is left None and the caller
    keeps its Black-Scholes delta estimate. Returns None if no usable credit quote.
    """
    import asyncio
    loop = asyncio.get_running_loop()

    def _fetch() -> Optional[dict]:
        try:
            import yfinance as yf
            from datetime import date as _date
            tk = yf.Ticker(symbol)
            expirations = list(tk.options or [])
            if not expirations:
                return None
            target = _date.fromisoformat(target_expiry_iso)
            chosen = min(expirations,
                         key=lambda e: abs((_date.fromisoformat(e) - target).days))
            chain = tk.option_chain(chosen)
            df = chain.puts if opt_type == "put" else chain.calls
            if df is None or df.empty:
                return None

            def _leg(strike: float):
                idx = (df["strike"] - strike).abs().idxmin()
                row = df.loc[idx]
                bid = float(row.get("bid", 0) or 0)
                ask = float(row.get("ask", 0) or 0)
                last = float(row.get("lastPrice", 0) or 0)
                if bid > 0 and ask > 0 and ask >= bid:
                    mid = (bid + ask) / 2.0
                elif last > 0:
                    mid = last
                else:
                    mid = None
                return float(row["strike"]), mid

            s_strike, s_mid = _leg(short_strike)
            l_strike, l_mid = _leg(long_strike)
            if s_mid is None or l_mid is None or s_strike == l_strike:
                return None
            net = s_mid - l_mid
            if net <= 0:
                return None
            return {
                "net_credit":   round(net * 100, 2),
                "short_delta":  None,
                "short_strike": s_strike,
                "long_strike":  l_strike,
                "expiration":   chosen,
            }
        except Exception:
            return None

    return await loop.run_in_executor(None, _fetch)


async def _build_portfolio_risk_state(portfolio_value: float):
    """
    Real PortfolioRiskState for RiskManager.approve_trade()'s concentration
    check — positions_by_underlying/positions_by_sector grouped from actual
    open trades via portfolio_engine.position_risk_dollars()/sector_for()
    (the same logic /api/portfolio/heat uses), net Greeks from the live
    _greeks_tracker when available. SPY and QQQ both map to sector "Index"
    (see portfolio_engine.SECTORS), so trading both is correctly seen as one
    correlated bucket rather than two independent 25%-cap slots.
    """
    from app.services.risk_manager import PortfolioRiskState
    from app.services.portfolio_engine import position_risk_dollars, sector_for
    from app.core.database import AsyncSessionLocal
    from app.models.trade import Trade
    from sqlalchemy import select

    by_underlying: dict[str, float] = {}
    by_sector: dict[str, float] = {}
    open_count = 0
    try:
        async with AsyncSessionLocal() as session:
            open_trades = (await session.execute(
                select(Trade).where(Trade.status == "open")
            )).scalars().all()
        open_count = len(open_trades)
        for t in open_trades:
            u = (t.underlying or "?").upper()
            s = sector_for(u)
            r = position_risk_dollars(t)
            by_underlying[u] = by_underlying.get(u, 0.0) + r
            by_sector[s] = by_sector.get(s, 0.0) + r
    except Exception as exc:
        logger.warning("Could not load open positions for concentration check: %s", exc)

    net_delta = net_vega = net_theta = 0.0
    if _greeks_tracker is not None:
        try:
            net_delta = _greeks_tracker.net_delta()
            net_vega  = _greeks_tracker.net_vega()
            net_theta = _greeks_tracker.net_theta()
        except Exception:
            pass

    return PortfolioRiskState(
        net_delta=net_delta, net_vega=net_vega, net_theta=net_theta,
        open_position_count=open_count, portfolio_value=portfolio_value,
        positions_by_underlying=by_underlying, positions_by_sector=by_sector,
    )


async def _run_options_scan(symbol: str = "SPY", execute: bool = True) -> Optional[dict]:
    """
    Generate an options spread signal for `symbol` based on the current regime.

    Returns the built signal dict (whether or not it was executed), or None
    if nothing qualified for this symbol this cycle — lets a caller scan many
    symbols, collect every qualifying candidate, and rank them before
    executing any (see _run_options_scan_watchlist), the same shape
    _run_equity_scan already uses for the equity side.

    `execute=False` (used by the on-demand preview endpoint,
    POST /api/options/signals/scan, and by the multi-symbol watchlist scan
    below) computes and persists the same signal — same strategy
    classification, strikes, credit/debit, Greeks — but skips the
    handle_signal() dispatch, so building/ranking candidates can never itself
    submit a real order. Only an explicit execute=True call dispatches.

    The regime itself is still classified from SPY alone (_reclassify_regime)
    and reused as a shared proxy for every symbol scanned — a market-wide
    regime (volatility/trend backdrop) is a reasonable filter for which
    options structures make sense across a watchlist of large, liquid,
    broadly market-correlated names, the same reasoning already used to let
    QQQ reuse SPY's regime rather than building each symbol its own
    classifier. What IS symbol-specific: the underlying's own price
    bars/SMA/spot, its own option chain and quotes, and the concentration
    check below (positions_by_underlying/positions_by_sector — each symbol's
    own sector, via portfolio_engine.sector_for(), so scanning a wider
    watchlist can't silently stack correlated exposure past the sector cap
    any more than SPY+QQQ both landing under "Index" already could).

    Delegates strike selection, entry-condition gating, and the credit-vs-debit
    distinction to the real per-strategy classes in strategy_engine.py — the
    same ones the backtester and ML training pipeline use. Previously this
    function used one generic "0.30-delta short, 5pt wide" credit-spread
    template for whichever strategy the regime picked, which: never re-checked
    a strategy's own entry conditions (IV rank/RSI/ADX/SMA) live, used the
    wrong delta/width for bull_put_spread and bear_call_spread (0.20delta/10pt
    in strategy_engine.py, not 0.30/5), and for bull_call_debit_spread built a
    call *credit* spread (sell near, buy far) while labeling it "SELL_SPREAD"
    — the opposite of what that strategy actually is (buy the ATM leg, sell an
    OTM leg, a net debit). LOW_VOL_TRENDING — a common, currently-active regime
    — allows *only* bull_call_debit_spread, so this was live, not theoretical.

    Tries every regime-allowed strategy in declared order, not just the first
    — a regime like NORMAL_MEAN_REVERT allows both bull_put_spread and
    bear_call_spread, and the first candidate failing its own entry condition
    (e.g. price below the 20-day SMA, so no bullish bias) doesn't mean none of
    the regime's strategies have an edge — a bearish setup is exactly when a
    later bear_call candidate could qualify. Uses the first strategy (in
    regime order) whose own entry conditions pass.

    iron_condor is always skipped regardless of position in the regime's
    strategy list: its two-sided put+call structure isn't wired into
    trade_desk._execute_signal's order construction (2 legs only), so
    submitting it would silently open just one side.

    Routes through execution handler (manual/copilot/autopilot), same as before.
    """
    if _current_regime is None or not getattr(_current_regime, "options_allowed", False):
        # Not recorded per-symbol here (unlike every rejection below) — this
        # is a single market-wide gate, so recording it for all 59 watchlist
        # symbols every cycle would just be 59 duplicate entries. The regime's
        # own options_allowed state is already visible via /api/market/regime.
        return None
    try:
        import uuid
        import numpy as np
        import pandas as pd
        from datetime import datetime, timezone, timedelta, date
        from decimal import Decimal
        from app.broker.broker_factory import get_broker
        from app.broker.broker_interface import OptionsChain, OptionContract, Greeks as GK
        from app.services.options_pricer import BlackScholesPricer
        from app.services.trading_mode import trading_mode_manager
        from app.services.strategy_engine import (
            BullPutSpread, BearCallSpread, IronCondor, BullCallDebitSpread,
        )
        from app.services.equity_signal_engine import compute_indicators
        from app.api.routes.trade_desk import _fetch_portfolio_state, RiskGateError

        strategy_classes = {
            "bull_put_spread":        BullPutSpread,
            "bear_call_spread":       BearCallSpread,
            "iron_condor":            IronCondor,
            "bull_call_debit_spread": BullCallDebitSpread,
        }

        pricer     = BlackScholesPricer()
        dte_target = trading_mode_manager.config.dte_target or 30
        RISK_FREE  = 0.05

        # Get the underlying's bars via yfinance — no broker subscription needed.
        # limit=60 (not 30) so compute_indicators' own >=30-bar floor still
        # leaves headroom for the leading NaN run its rolling windows produce.
        underlying_bars = await _yf_bars(symbol, limit=60)
        if len(underlying_bars) < 30:
            await _record_options_rejection(symbol, "insufficient price history")
            return None

        closes = [float(b.close) for b in underlying_bars]
        spot   = closes[-1]
        sma20  = float(np.mean(closes[-20:]))
        above_sma20 = spot > sma20
        log_rets = np.diff(np.log(closes))
        sigma   = float(np.std(log_rets) * np.sqrt(252))

        # RSI and ADX are now THIS symbol's own values, computed the same way
        # the equity scanner already computes them for these same 59
        # watchlist tickers — not a single market-wide reading applied
        # uniformly to all of them. Previously every symbol shared one RSI
        # and one ADX off the regime classifier, so a strategy's own entry
        # gate (e.g. bull_call_debit_spread's RSI>55) either passed for the
        # WHOLE watchlist at once or failed for all of it together,
        # regardless of any individual stock's real condition — the
        # reachable cause of options signals almost never firing in
        # production (confirmed: one options trade total, ever, vs. the
        # equity side firing continuously on the same tickers).
        _ind_df = pd.DataFrame([{
            "open": float(b.open), "high": float(b.high),
            "low": float(b.low), "close": float(b.close), "volume": b.volume,
        } for b in underlying_bars])
        _ind = compute_indicators(_ind_df)
        rsi = float(_ind.get("rsi", 50.0)) if _ind else 50.0
        adx = float(_ind.get("adx", 20.0)) if _ind else 20.0

        # IV rank stays a shared, market-wide vol-regime proxy (VIX-derived) —
        # unlike RSI/ADX, a real per-symbol equivalent needs actual per-stock
        # options history, which isn't reliably available here without a paid
        # data feed. This is a smaller compromise than RSI/ADX were: implied
        # vol genuinely co-moves across the market far more than momentum
        # does, so one shared reading is a defensible approximation rather
        # than the same-number-for-every-stock problem RSI/ADX had.
        feat    = _current_regime.features_used
        iv_rank = float(getattr(feat, "iv_rank", 30.0)) if feat else 30.0
        vix_pct = float(getattr(feat, "vix", sigma * 100)) if feat else sigma * 100
        vix_est = vix_pct / 100.0

        # Real, fail-closed portfolio state — generate_signal()'s own guardrail
        # check needs this; skip the cycle rather than trade on stale/unknown risk.
        try:
            portfolio_state = await _fetch_portfolio_state()
        except RiskGateError as exc:
            logger.warning("Options scan: risk gate unavailable — skipping cycle: %s", exc)
            return None

        # Target expiry ~dte_target days out, on a Friday
        today      = date.today()
        target_exp = today + timedelta(days=dte_target)
        while target_exp.weekday() != 4:   # roll to Friday
            target_exp += timedelta(days=1)
        T = max((target_exp - today).days / 365, 0.01)

        # Synthetic strike-grid chain (Black-Scholes) — same construction the
        # backtester uses — so strategy_obj's own _find_strike_by_delta() picks
        # its own real target delta/strikes instead of a hardcoded 0.30/5pt.
        calls: list = []
        puts: list = []
        for offset in range(-60, 61, 5):
            s = float(round(spot + offset))
            try:
                put_px  = pricer.put_price(spot, s, T, RISK_FREE, vix_est)
                call_px = pricer.call_price(spot, s, T, RISK_FREE, vix_est)
                put_d   = pricer.delta(spot, s, T, RISK_FREE, vix_est, "put")
                call_d  = pricer.delta(spot, s, T, RISK_FREE, vix_est, "call")
                gamma_v = pricer.gamma(spot, s, T, RISK_FREE, vix_est)
                put_th  = pricer.theta(spot, s, T, RISK_FREE, vix_est, "put")
                call_th = pricer.theta(spot, s, T, RISK_FREE, vix_est, "call")
                vega_v  = pricer.vega(spot, s, T, RISK_FREE, vix_est)
            except Exception:
                continue
            half_spread = 0.05
            puts.append(OptionContract(
                symbol=f"{symbol}_P{int(s)}", underlying=symbol, expiration=target_exp,
                strike=Decimal(str(int(s))), option_type="put",
                bid=Decimal(str(round(max(put_px - half_spread, 0.01), 2))),
                ask=Decimal(str(round(put_px + half_spread, 2))),
                last=Decimal(str(round(put_px, 2))), volume=1000, open_interest=5000,
                greeks=GK(delta=put_d, gamma=gamma_v, theta=put_th, vega=vega_v, implied_vol=vix_est),
            ))
            calls.append(OptionContract(
                symbol=f"{symbol}_C{int(s)}", underlying=symbol, expiration=target_exp,
                strike=Decimal(str(int(s))), option_type="call",
                bid=Decimal(str(round(max(call_px - half_spread, 0.01), 2))),
                ask=Decimal(str(round(call_px + half_spread, 2))),
                last=Decimal(str(round(call_px, 2))), volume=1000, open_interest=5000,
                greeks=GK(delta=call_d, gamma=gamma_v, theta=call_th, vega=vega_v, implied_vol=vix_est),
            ))
        chain = OptionsChain(
            underlying=symbol, expiration=target_exp,
            underlying_price=Decimal(str(round(spot, 2))),
            calls=calls, puts=puts, fetched_at=datetime.now(timezone.utc),
        )

        # Try every regime-allowed strategy in order, not just the first — a
        # regime often allows more than one shape (e.g. NORMAL_MEAN_REVERT
        # allows both bull_put_spread and bear_call_spread). Previously the
        # scan gave up the moment strategies_allowed[0] failed its own entry
        # condition (e.g. "no bullish bias"), even when a later-listed
        # strategy in the same regime might actually qualify given current
        # price action (a bearish setup is exactly when a later bear_call
        # candidate could have an edge).
        candidates = _current_regime.strategies_allowed or ["bull_put_spread"]
        strategy_name: Optional[str] = None
        strategy_obj = None
        signal_obj = None
        rejections: list[str] = []
        for name in candidates:
            cls = strategy_classes.get(name)
            if cls is None:
                logger.warning("Options scan: unknown strategy %r — skipping", name)
                continue
            if name == "iron_condor":
                # Intentionally skipped: its 2-sided put+call structure isn't
                # wired into _execute_signal's order construction (2 legs only).
                continue
            obj = cls()
            sig = obj.generate_signal(
                chain=chain, iv_rank=iv_rank, rsi=rsi, adx=adx,
                above_sma20=above_sma20, vix=vix_pct, portfolio_state=portfolio_state,
            )
            if sig.entry_allowed and sig.short_strike:
                strategy_name, strategy_obj, signal_obj = name, obj, sig
                break
            rejections.append(f"{name}: {sig.reason}")

        if strategy_obj is None:
            _reason = "; ".join(rejections) or "no candidates"
            logger.info(
                "Options scan %s: no regime-allowed strategy qualified — %s",
                symbol, _reason,
            )
            await _record_options_rejection(symbol, _reason)
            return None

        from app.api.routes.equity import _recent_signals as _equity_signals
        _confluence_reject = _equity_confluence_reason(
            symbol, strategy_name, _equity_signals, datetime.now(timezone.utc),
        )
        if _confluence_reject:
            logger.info("Options scan %s: %s %s — skipping",
                        symbol, strategy_name, _confluence_reject)
            await _record_options_rejection(symbol, _confluence_reject, strategy_name)
            return None

        is_debit = strategy_name == "bull_call_debit_spread"
        is_call  = strategy_name in ("bear_call_spread", "bull_call_debit_spread")
        opt_type = "call" if is_call else "put"

        short_strike = signal_obj.short_strike
        long_strike  = signal_obj.long_strike or (
            short_strike + 10.0 if is_call else short_strike - 10.0
        )
        spread_width = abs(short_strike - long_strike)
        best_short_delta = signal_obj.target_delta or 0.20

        # Black-Scholes price at the strategy's own chosen strikes (fallback / no-subscription path)
        # Net delta/vega (long leg minus short leg — the position is short the
        # short_strike leg, long the long_strike leg, uniformly across all 4
        # strategies) feed the portfolio-level concentration check below.
        try:
            short_px = pricer.call_price(spot, short_strike, T, RISK_FREE, vix_est) if is_call \
                       else pricer.put_price(spot, short_strike, T, RISK_FREE, vix_est)
            long_px  = pricer.call_price(spot, long_strike, T, RISK_FREE, vix_est) if is_call \
                       else pricer.put_price(spot, long_strike, T, RISK_FREE, vix_est)
            net_amount = round((short_px - long_px) * 100, 2)  # +credit / -debit, per contract $
            _greek_opt = "call" if is_call else "put"
            net_position_delta = (
                pricer.delta(spot, long_strike, T, RISK_FREE, vix_est, _greek_opt)
                - pricer.delta(spot, short_strike, T, RISK_FREE, vix_est, _greek_opt)
            )
            net_position_vega = (
                pricer.vega(spot, long_strike, T, RISK_FREE, vix_est)
                - pricer.vega(spot, short_strike, T, RISK_FREE, vix_est)
            )
        except Exception:
            net_amount = 0.0
            net_position_delta = net_position_vega = 0.0

        # Prefer the LIVE chain price when a quote is available — the limit then
        # tracks where the spread can actually fill instead of a theoretical mid.
        # Both fallback helpers reject non-positive "credit", which only holds
        # for the 3 credit strategies — bull_call_debit_spread stays on the
        # Black-Scholes estimate above instead of risking a sign mixup reusing
        # credit-only helpers for a debit trade.
        credit_source = "black_scholes"
        # Real per-leg bid/ask/OI/gamma, only ever populated on the live-chain
        # path (below) — the liquidity/gamma gates in _execute_signal() must
        # see None on every other path rather than a fabricated value.
        liquidity_data: Optional[dict] = None
        broker = get_broker()
        if not is_debit:
            live = await _live_spread_quote(
                broker, symbol, target_exp.isoformat(), short_strike, long_strike, opt_type,
            )
            if live and live["net_credit"] > 0:
                short_strike = live["short_strike"]
                long_strike  = live["long_strike"]
                spread_width = abs(short_strike - long_strike)
                net_amount   = live["net_credit"]
                if live["short_delta"] is not None:
                    best_short_delta = live["short_delta"]
                credit_source = "live_chain"
                liquidity_data = live
            else:
                yq = await _yf_options_quote(
                    symbol, target_exp.isoformat(), short_strike, long_strike, opt_type,
                )
                if yq and yq["net_credit"] > 0:
                    short_strike = yq["short_strike"]
                    long_strike  = yq["long_strike"]
                    spread_width = abs(short_strike - long_strike)
                    net_amount   = yq["net_credit"]
                    target_exp   = date.fromisoformat(yq["expiration"])
                    credit_source = "yfinance_chain"

        if spread_width <= 0:
            logger.info("Options scan: no usable spread width — skipping")
            await _record_options_rejection(symbol, "no usable spread width", strategy_name)
            return None
        if is_debit:
            net_debit = -net_amount
            if net_debit <= 0.05:
                logger.info("Options scan: debit too small ($%.2f) — skipping", net_debit)
                await _record_options_rejection(
                    symbol, f"debit too small (${net_debit:.2f})", strategy_name,
                )
                return None
        elif net_amount <= 0.05:
            logger.info("Options scan: no usable spread (width=%.1f credit=%.2f) — skipping",
                        spread_width, net_amount)
            await _record_options_rejection(
                symbol,
                f"no usable spread (width={spread_width:.1f}, credit=${net_amount:.2f})",
                strategy_name,
            )
            return None

        credit_per_share = net_amount / 100.0
        max_loss_dollars = (
            round(abs(net_amount), 2) if is_debit
            else round((spread_width - credit_per_share) * 100, 2)
        )

        # ── AI signal scoring gate ──────────────────────────────────────────
        # Mirror the equity path: an options spread must pass the scorer before
        # it is routed to execution. Previously options bypassed the scorer with
        # signal_score=0, so every spread that the regime allowed was traded.
        global _signal_scorer
        if _signal_scorer is None:
            from app.services.signal_scorer import SignalScorer
            _signal_scorer = SignalScorer()
        from app.services.signal_scorer import SignalFeatures

        features = SignalFeatures(
            iv_rank=iv_rank,
            iv_percentile=iv_rank,
            vix_level=vix_pct,
            spy_rsi_14=rsi,
            spy_adx_14=adx,
            spy_trend_direction=1.0 if above_sma20 else -1.0,
            days_to_expiry=float(dte_target),
            short_strike_delta=float(best_short_delta),
            spread_width=float(spread_width),
            credit_to_width_ratio=(net_amount / 100.0 / spread_width) if spread_width else 0.0,
            earnings_days_away=60.0,   # SPY/QQQ (ETFs) — no single-name earnings event
            spy_realized_vol_20d=float(sigma),
            iv_minus_rv=float(vix_est - sigma),
        )
        score_result = await _signal_scorer.score_async(features)
        signal_score = float(score_result.score)
        # SHAP feature attribution — cheap (feature_impacts is already computed
        # as part of score_async() above, this just formats it), attached to
        # both the reject and approve paths below so a user can see *why* a
        # signal scored the way it did, not just the numeric score.
        evidence = _signal_scorer.explain(score_result)
        if not score_result.approved and not settings.execution_test_mode:
            logger.info(
                "Options signal rejected by AI scorer: %s %s score=%.3f — %s",
                symbol, strategy_name, signal_score, score_result.rejection_reason,
            )
            await _record_options_rejection(
                symbol,
                f"AI scorer: {score_result.rejection_reason or f'score {signal_score:.3f}'}",
                strategy_name,
                evidence=evidence,
            )
            return None
        if not score_result.approved:
            logger.warning(
                "EXECUTION_TEST_MODE: routing options signal %s %s despite AI score=%.3f",
                symbol, strategy_name, signal_score,
            )

        # ── Position sizing via RiskManager ─────────────────────────────────
        # Size off portfolio value, the spread's max loss, the active trading
        # mode's risk-per-trade %, and the regime's options size multiplier —
        # same machinery the equity path uses.
        from app.services.risk_manager import RiskManager
        from app.services.account_state import get_account_value
        portfolio_value = await get_account_value()
        risk_pct    = trading_mode_manager.config.risk_per_trade_pct
        regime_mult = float(getattr(_current_regime, "options_size_multiplier", 1.0))
        # Volatility-based sizing: scale the regime budget inversely with vol so
        # dollar risk stays steady across calm/fearful tape (Batch C).
        from app.services.volatility_sizing import vol_adjusted_multiplier, describe as _vol_desc
        size_mult = vol_adjusted_multiplier(regime_mult, iv_rank, vix_pct)
        logger.info("Vol sizing: %s → mult %.2f×%.2f=%.2f",
                    _vol_desc(iv_rank, vix_pct)["stance"],
                    regime_mult, size_mult / max(regime_mult, 1e-9), size_mult)
        quantity = RiskManager().calculate_position_size(
            portfolio_value=portfolio_value,
            max_loss_per_spread=max_loss_dollars,
            risk_pct=risk_pct,
            size_multiplier=size_mult,
        )
        if quantity <= 0:
            logger.info(
                "Options signal skipped — sized to 0 contracts "
                "(portfolio=$%.0f max_loss=$%.0f risk_pct=%.3f mult=%.2f)",
                portfolio_value, max_loss_dollars, risk_pct, size_mult,
            )
            await _record_options_rejection(
                symbol, f"sized to 0 contracts (max loss ${max_loss_dollars:.0f})", strategy_name,
            )
            return None

        # ── Single-underlying / sector concentration check ──────────────────
        # Every symbol maps to its own sector (portfolio_engine.SECTORS —
        # SPY/QQQ both land under "Index"), so scanning a wider watchlist
        # can't silently stack correlated exposure past the sector cap any
        # more than SPY+QQQ already could. Deliberately narrower than
        # RiskManager.approve_trade():
        # that method also gates on portfolio delta/vega, but those limits
        # have never been checked against a real trade before (approve_trade
        # was never called from any live path) and a sanity check found a
        # standard single 10-point-wide spread already exceeds the vega cap
        # (0.15) on its own — a separate, pre-existing calibration issue, not
        # something to silently work around here. Only the concentration
        # logic is reused; net_position_delta/vega stay unused for now.
        from app.services.portfolio_engine import is_cappable_sector, sector_for
        portfolio_risk = await _build_portfolio_risk_state(portfolio_value)
        trade_sector = sector_for(symbol)
        total_new_risk = max_loss_dollars * quantity
        if portfolio_risk.portfolio_value > 0:
            new_underlying_exposure = (
                portfolio_risk.positions_by_underlying.get(symbol, 0.0) + total_new_risk
            )
            underlying_pct = new_underlying_exposure / portfolio_risk.portfolio_value
            if underlying_pct > RiskManager.MAX_SINGLE_UNDERLYING:
                logger.info(
                    "Options signal blocked — %s concentration would be %.1f%% "
                    "of portfolio (max %.0f%%)",
                    symbol, underlying_pct * 100, RiskManager.MAX_SINGLE_UNDERLYING * 100,
                )
                await _record_options_rejection(
                    symbol,
                    f"{symbol} concentration would be {underlying_pct * 100:.1f}% of portfolio "
                    f"(max {RiskManager.MAX_SINGLE_UNDERLYING * 100:.0f}%)",
                    strategy_name,
                )
                return None
            new_sector_exposure = (
                portfolio_risk.positions_by_sector.get(trade_sector, 0.0) + total_new_risk
            )
            sector_pct = new_sector_exposure / portfolio_risk.portfolio_value
            # Unknown is the absence of a sector, not a sector — capping it
            # blocks on how much of the book is unclassified. See
            # portfolio_engine.is_cappable_sector.
            if is_cappable_sector(trade_sector) and sector_pct > RiskManager.MAX_SECTOR_CONCENTRATION:
                logger.info(
                    "Options signal blocked — sector %r (%s) concentration would be "
                    "%.1f%% of portfolio (max %.0f%%)",
                    trade_sector, symbol, sector_pct * 100, RiskManager.MAX_SECTOR_CONCENTRATION * 100,
                )
                await _record_options_rejection(
                    symbol,
                    f"sector {trade_sector!r} concentration would be {sector_pct * 100:.1f}% "
                    f"of portfolio (max {RiskManager.MAX_SECTOR_CONCENTRATION * 100:.0f}%)",
                    strategy_name,
                )
                return None

        # ── Options Intelligence: real POP / EV / Kelly for this spread ─────────
        # Drives the frequency controller's quality filter with the true
        # probability of profit instead of a proxy. analyze_spread() clamps
        # credit_per_share to >= 0, so it isn't meaningful for a debit spread
        # (credit_per_share is negative there) — skip rather than feed it a
        # floored-to-zero credit and get a fabricated POP/Kelly back.
        intel = None
        if not is_debit:
            try:
                from app.services.options_intelligence import analyze_spread
                intel = analyze_spread(
                    spot=spot, short_strike=short_strike, long_strike=long_strike,
                    option_type=opt_type, dte=float(dte_target), iv=vix_est,
                    credit_per_share=credit_per_share, r=RISK_FREE,
                )
            except Exception as _intel_exc:
                logger.debug("Options intelligence failed: %s", _intel_exc)

        if is_debit:
            breakeven = round(long_strike - credit_per_share, 2)
        elif is_call:
            breakeven = round(short_strike + credit_per_share, 2)
        else:
            breakeven = round(short_strike - credit_per_share, 2)

        # Derived liquidity/gamma fields — None unless liquidity_data was
        # populated on the live-chain path above. Never fabricated on the
        # yfinance/Black-Scholes fallback paths; downstream gates in
        # _execute_signal() must treat None as "skip the check."
        bid_ask_width_pct: Optional[float] = None
        spread_open_interest: Optional[int] = None
        spread_gamma: Optional[float] = None
        if liquidity_data:
            short_bid = liquidity_data["short_bid"]
            short_ask = liquidity_data["short_ask"]
            short_mid = (short_bid + short_ask) / 2.0
            if short_mid > 0:
                bid_ask_width_pct = round((short_ask - short_bid) / short_mid, 4)
            # The illiquid leg governs whether the whole spread can actually
            # fill — take the thinner of the two.
            spread_open_interest = min(
                liquidity_data["short_open_interest"], liquidity_data["long_open_interest"],
            )
            spread_gamma = liquidity_data["short_gamma"]

        signal = {
            "id":           str(uuid.uuid4()),
            "ticker":       symbol,
            "asset_type":   "options",
            "source":       "Options Signal Scanner",
            "strategy":     strategy_name,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "action":       "BUY_SPREAD" if is_debit else "SELL_SPREAD",
            # Confidence = real probability of profit when available; the frequency
            # controller then computes EV = POP·reward − (1−POP) directly.
            "confidence":   round(intel.pop, 4) if intel else round(getattr(_current_regime, "confidence", 0.5), 4),
            "pop":          round(intel.pop, 4) if intel else None,
            "kelly_fraction": intel.kelly_fraction if intel else None,
            "intelligence": intel.as_dict() if intel else None,
            "signal_score": round(signal_score, 4),
            "quantity":     int(quantity),
            "iv_rank":      round(iv_rank, 2),
            "regime":       _current_regime.regime.value,
            "evidence": {
                "top_positive_factors": evidence["top_positive_factors"],
                "top_negative_factors": evidence["top_negative_factors"],
            },
            "spread": {
                "option_type":   opt_type,
                "short_strike":  short_strike,
                "long_strike":   long_strike,
                "expiration":    target_exp.isoformat(),
                "dte":           dte_target,
                # Signed: positive = net credit received, negative = net debit
                # paid. The broker layer (ibkr_client.place_order) and P&L
                # recording (trade_recorder.record_exit) already expect this
                # convention — see their docstrings.
                "net_credit":    round(net_amount, 2),
                "max_loss":      max_loss_dollars,
                "breakeven":     breakeven,
                # Real per-leg liquidity/gamma — only populated on the live
                # IBKR chain path; None on yfinance/Black-Scholes fallback.
                "bid_ask_width_pct": bid_ask_width_pct,
                "open_interest":     spread_open_interest,
                "gamma":             spread_gamma,
                "quote_source":      credit_source,
            },
            "sigma":         round(sigma, 4),
            "vix_used":      round(vix_est * 100, 1),
            "credit_source": credit_source,
        }
        from app.services.opportunity_score import compute_opportunity_score
        signal["opportunity_score"] = compute_opportunity_score(signal)

        # Wire the alert rule-evaluation engine — options signals had no
        # caller into evaluate_symbol() at all (unlike equity, wired in
        # Phase 1). alpha_edge_entry_score mirrors the real Alpha Edge
        # options orchestrator's own "no position/history yet" formula
        # (round(signal_score * 100)) rather than re-deriving it a third
        # way; alpha_edge_risk_score reuses the exact same pure-math
        # function (trade_frequency_controller.risk_score) that
        # orchestrator itself calls — no re-fetch of its I/O-bound flow.
        try:
            from app.services.trade_frequency_controller import risk_score as _ae_risk_score
            alert_snapshot = {
                "price":                  spot,
                "iv_rank":                round(iv_rank, 2),
                "vix_used":               round(vix_est * 100, 1),
                "alpha_edge_entry_score": round(signal_score * 100),
                "alpha_edge_risk_score":  _ae_risk_score(signal),
                "opportunity_score":      signal["opportunity_score"]["score"],
            }
            from app.services.alerts.service import evaluate_symbol
            await evaluate_symbol(symbol, alert_snapshot)
        except Exception as exc:
            logger.warning("Alert evaluation failed for %s: %s", symbol, exc)

        logger.info(
            "Options signal: %s %s %s %s/%s exp %s %s $%.2f (%s) score=%.3f qty=%d",
            strategy_name, symbol, opt_type, short_strike, long_strike, target_exp,
            "debit" if is_debit else "credit", abs(net_amount), credit_source,
            signal_score, quantity,
        )

        # Persist for the Options Signals UI (mirrors equity._recent_signals).
        from app.api.routes.options import _recent_options_signals
        _recent_options_signals.insert(0, signal)
        del _recent_options_signals[200:]

        # Also persist to the DB so this signal survives a backend restart —
        # the in-memory store above doesn't. Mirrors equity's record_signal()
        # (called unconditionally of execute, same as the in-memory insert
        # above already is).
        from app.services.options_signal_history import record_options_signal
        await record_options_signal(signal)

        if execute:
            from app.api.routes.trade_desk import handle_signal
            await handle_signal(signal)

        return signal

    except Exception as exc:
        logger.warning("Options scan failed: %s", exc)
        # Generic, user-facing reason — the real exception (which may include
        # internals not worth surfacing) stays in the server log above.
        await _record_options_rejection(symbol, "scan error — see server log")
        return None


async def _run_options_scan_watchlist() -> None:
    """
    Background options scan across the full equity watchlist, not just
    SPY/QQQ. Builds every symbol's candidate signal without executing
    (execute=False), collects whichever qualified, ranks them by the same
    weighted quality score _run_equity_scan uses, then routes highest-first
    through the real execution handler — so on a cycle where several symbols
    qualify, the best-scoring spread gets first claim on the daily cap and
    open-position capacity instead of whichever symbol happened to be
    scanned first (previously just SPY then QQQ, in that fixed order).

    The daily trade cap and portfolio gate inside handle_signal already stop
    execution once capacity is reached, so scanning more symbols can't itself
    over-trade — it only gives the frequency controller more, better
    candidates to choose from each cycle.
    """
    watchlist = settings.get_equity_watchlist()
    # Bounded concurrency — same rationale as _run_equity_scan: the watchlist
    # widened from 13 to ~59 symbols, and this call is wrapped in a 240s hard
    # timeout (_guarded, in the scheduler). A sequential loop over 59 symbols
    # (each doing a full options-chain fetch, heavier than an equity bars
    # fetch) could plausibly exceed 240s — and asyncio.wait_for cancels the
    # whole coroutine on timeout, discarding every candidate found so far,
    # not just the tail of the list. A semaphore keeps this well under the
    # timeout instead of risking losing the entire cycle's scan.
    semaphore = asyncio.Semaphore(max(1, settings.equity_scan_concurrency))

    async def _scan_one(symbol: str) -> Optional[dict]:
        async with semaphore:
            try:
                from app.broker.ibkr_coordinator import Priority, ibkr_coordinator
                return await ibkr_coordinator.submit(
                    Priority.P2, lambda s=symbol: _run_options_scan(s, execute=False),
                    timeout=60.0, req_type="OPTIONS_SCAN", symbol=symbol,
                )
            except Exception as exc:
                logger.warning("Options scan failed for %s: %s", symbol, exc)
                return None

    results = await asyncio.gather(*[_scan_one(s) for s in watchlist])
    candidates: list[dict] = [sig for sig in results if sig is not None]

    if not candidates:
        return

    from app.services.trade_frequency_controller import trade_frequency_controller
    from app.api.routes.trade_desk import handle_signal
    for sig in trade_frequency_controller.rank(candidates):
        try:
            await handle_signal(sig)
        except Exception as exec_exc:
            logger.warning("Options execution handler failed for %s: %s",
                           sig.get("ticker"), exec_exc)


def _fill_after_entry(fill_time, entry_dt) -> bool:
    """True if an execution happened at/after the trade's entry (tz-safe)."""
    if fill_time is None or entry_dt is None:
        return True   # can't tell — don't exclude
    try:
        from datetime import timezone as _tz
        ft = fill_time if fill_time.tzinfo else fill_time.replace(tzinfo=_tz.utc)
        ed = entry_dt if entry_dt.tzinfo else entry_dt.replace(tzinfo=_tz.utc)
        return ft >= ed
    except Exception:
        return True


def _compute_exit_price(fills: list, trade, is_equity: bool):
    """
    Reconstruct the per-share cost-to-close from real IBKR executions.

    Only counts the CLOSING-side fills that occurred at/after entry, weighted by
    shares — never averages the opening fill into the exit (the old bug). Returns
    None when the close cannot be reliably reconstructed, so the caller books an
    unknown P&L instead of a fabricated number.
    """
    if not fills:
        return None
    entry_dt = getattr(trade, "entry_date", None)

    def _wavg(rows) -> Optional[float]:
        sh = sum(r["shares"] for r in rows)
        if sh <= 0:
            return None
        return sum(r["price"] * r["shares"] for r in rows) / sh

    if is_equity:
        # Long closes with a SELL (SLD); short closes with a BUY (BOT).
        close_side = "BOT" if (trade.spread_type or "").lower() == "equity_short" else "SLD"
        rows = [
            f for f in fills
            if f["secType"] in ("STK", "") and f["side"] == close_side
            and _fill_after_entry(f["time"], entry_dt)
        ]
        px = _wavg(rows)
        return round(px, 4) if px is not None else None

    # Options credit spread: close = buy back the short leg + sell the long leg.
    # cost_to_close (per share, net debit) = short_buyback − long_sale.
    right = "C" if (trade.spread_type or "").lower().startswith("c") else "P"
    short_k = float(trade.short_strike or 0)
    long_k  = float(trade.long_strike or 0)

    def _leg(strike: float, side: str) -> Optional[float]:
        rows = [
            f for f in fills
            if f["secType"] == "OPT" and f["right"] == right
            and abs(f["strike"] - strike) < 0.01 and f["side"] == side
            and _fill_after_entry(f["time"], entry_dt)
        ]
        return _wavg(rows)

    short_close = _leg(short_k, "BOT")   # bought back the short leg
    long_close  = _leg(long_k, "SLD")    # sold the long leg
    if short_close is None or long_close is None:
        return None
    return round(short_close - long_close, 4)


async def _poll_fills() -> None:
    """
    Compare live broker positions against open DB trades.

    When a position disappears from IBKR (fully closed), reconstruct the actual
    cost-to-close from IBKR execution history and record the real P&L. If a real
    exit price cannot be determined, the trade is held open and retried for a
    grace window; only after that does it close with UNKNOWN P&L (never a
    fabricated $0), with a CRITICAL alert for manual reconciliation.
    """
    try:
        import asyncio
        from datetime import datetime, timezone
        from app.broker.broker_factory import get_broker
        from app.core.database import AsyncSessionLocal
        from app.models.trade import Trade
        from app.services.trade_recorder import trade_recorder
        from sqlalchemy import select

        broker = get_broker()

        # Fail closed on a stale/disconnected broker rather than risk a mass
        # false-close. IBKRClient.get_positions() reads ib.portfolio() — a
        # purely local cache with no network round-trip and no exception on
        # staleness — so right after a reconnect it can return an empty or
        # incomplete list *without raising*, before IBKR's account-update
        # subscription has resynced it. Confirmed in production: six
        # unrelated open positions (SPY, META, AMZN, NVDA, MSFT, AAPL) all
        # got marked closed_price_unavailable within 30ms of each other in a
        # single poll cycle — the signature of "the position list came back
        # empty because the cache was stale," not six coincidental real
        # closes. If the broker can report its own connection state and says
        # it isn't connected, skip this cycle entirely rather than treat a
        # hollow position list as ground truth.
        broker_ib = getattr(broker, "ib", None)
        if broker_ib is not None and not (
            getattr(broker, "_connected", False) and broker_ib.isConnected()
        ):
            logger.debug("_poll_fills: broker not connected — skipping reconciliation this cycle")
            return

        live_positions = await broker.get_positions()

        # Build set of symbols currently held at IBKR
        live_symbols = {
            getattr(p, "symbol", getattr(p, "underlying", "")).upper()
            for p in live_positions
        }

        # Sum live unrealized P&L per underlying (for MFE/MAE excursion tracking).
        live_upnl: dict[str, float] = {}
        for p in live_positions:
            sym = getattr(p, "symbol", getattr(p, "underlying", "")).upper()
            upnl = getattr(p, "unrealized_pnl", None)
            if sym and upnl is not None:
                live_upnl[sym] = live_upnl.get(sym, 0.0) + float(upnl)

        # Sum live quantity per underlying — confirm_fill() needs this to
        # correct Trade.quantity to what the broker actually holds. Without
        # it, a pending trade promoted to open keeps whatever quantity was
        # planned at signal time forever, even if the real fill (or residual
        # shares from prior activity on the same ticker) came in different.
        live_qty: dict[str, float] = {}
        for p in live_positions:
            sym = getattr(p, "symbol", getattr(p, "underlying", "")).upper()
            qty = getattr(p, "quantity", None)
            if sym and qty is not None:
                live_qty[sym] = live_qty.get(sym, 0.0) + abs(float(qty))

        # Load all open + pending trades from DB
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Trade).where(Trade.status.in_(["open", "pending"]))
            )
            all_trades = result.scalars().all()

        open_trades    = [t for t in all_trades if t.status == "open"]
        pending_trades = [t for t in all_trades if t.status == "pending"]

        if not open_trades and not pending_trades:
            _close_pending.clear()
            return

        # Fetch recent IBKR executions once — detailed per-fill records so we can
        # match the closing side / specific option legs (not a blind all-time avg).
        fills_by_symbol: dict[str, list] = {}
        try:
            ib = getattr(broker, "ib", None)
            if ib is not None:
                from ib_insync import ExecutionFilter
                fills = await asyncio.wait_for(
                    ib.reqExecutionsAsync(ExecutionFilter()),
                    timeout=5.0,
                )
                for fill in fills:
                    c  = fill.contract
                    ex = fill.execution
                    sym = (c.symbol or "").upper()
                    rec = {
                        "secType": c.secType or "",
                        "strike":  float(c.strike or 0),
                        "right":   (c.right or "").upper(),
                        "side":    (ex.side or "").upper(),   # BOT / SLD
                        "price":   float(ex.price or 0),
                        "shares":  abs(float(ex.shares or 0)),
                        "time":    getattr(ex, "time", None),
                    }
                    if sym and rec["shares"] > 0 and rec["price"] > 0:
                        fills_by_symbol.setdefault(sym, []).append(rec)
        except Exception as _exec_exc:
            logger.debug("Could not fetch IBKR executions: %s", _exec_exc)

        now = datetime.now(timezone.utc)

        # Symbols whose trade rows this pass books closed or cancels — their
        # resting orders have to be cancelled too, see the sweep at the end.
        closed_symbols: set[str] = set()

        # ── Entry-side reconciliation: resolve pending (working) orders ─────────
        # A pending trade is promoted to `open` once its position shows at the
        # broker (or a fill execution is found). If, after the grace window, there
        # is still no position and no fill, the order terminated unfilled (DAY
        # expiry / ProgramCancel) → cancel it so it never becomes a phantom.
        #
        # The grace-period clock is derived from trade.entry_date (persisted),
        # not an in-memory "first seen" timestamp — a backend restart mid-grace
        # (e.g. a routine deploy) used to reset the in-memory clock, leaving a
        # stuck pending trade blocking the duplicate-open guard for up to
        # another full FILL_GRACE_SECONDS after the restart, on top of however
        # long it had already been stuck. Confirmed in production: an
        # unfilled NVDA bracket order stayed pending well past 30 minutes and
        # blocked every subsequent NVDA signal with "skipped: already_open"
        # because a deploy landed mid-grace-period and restarted its timer.
        for trade in pending_trades:
            underlying = (trade.underlying or "").upper()
            tid = str(trade.id)
            if not underlying:
                continue
            filled = underlying in live_symbols or bool(fills_by_symbol.get(underlying))
            if filled:
                await trade_recorder.confirm_fill(
                    trade_id=tid, quantity=live_qty.get(underlying),
                )
                continue
            entry_dt = trade.entry_date
            if entry_dt.tzinfo is None:
                entry_dt = entry_dt.replace(tzinfo=timezone.utc)
            if (now - entry_dt).total_seconds() >= FILL_GRACE_SECONDS:
                await trade_recorder.cancel_pending(
                    trade_id=tid, reason="order_unfilled_timeout",
                )
                # cancel_pending only flips the DB row to "cancelled" — it does
                # not tell the broker anything. The comment above assumed the
                # order had already terminated on its own (DAY expiry), but
                # nothing here can distinguish "terminated" from "still
                # working", so the sweep below cancels it for real.
                closed_symbols.add(underlying)

        still_missing: set[str] = set()

        for trade in open_trades:
            underlying = (trade.underlying or "").upper()
            tid = str(trade.id)
            if not underlying or underlying in live_symbols:
                _close_pending.pop(tid, None)
                # Still open at broker — advance MFE/MAE from live unrealized P&L.
                if underlying in live_upnl:
                    await trade_recorder.update_excursion(
                        trade_id=tid, unrealized_pnl=live_upnl[underlying],
                    )
                continue  # still open at broker

            still_missing.add(tid)
            closed_symbols.add(underlying)

            spread_type = (trade.spread_type or "").lower()
            is_equity   = (trade.strategy == "equity") or spread_type.startswith("equity")
            exit_price  = _compute_exit_price(fills_by_symbol.get(underlying), trade, is_equity)

            if exit_price is not None:
                # Name the leg that actually filled where the row can prove it.
                # Falls back to the neutral label rather than guessing — see
                # exit_classifier for why that asymmetry is deliberate.
                from app.services.exit_classifier import classify_broker_exit

                resolved_reason = classify_broker_exit(trade, exit_price)
                await trade_recorder.record_exit(
                    trade_id=tid,
                    cost_to_close=exit_price,
                    exit_reason=resolved_reason,
                )
                _close_pending.pop(tid, None)
                logger.info(
                    "Auto-closed %s (%s) — cost_to_close=%.4f reason=%s "
                    "source=ibkr_execution",
                    tid, underlying, exit_price, resolved_reason,
                )
                continue

            # No reliable exit price yet — wait within the grace window before
            # booking unknown, in case the execution report arrives shortly.
            first_missing = _close_pending.setdefault(tid, now)
            elapsed = (now - first_missing).total_seconds()
            if elapsed < CLOSE_GRACE_SECONDS:
                logger.warning(
                    "Position %s (%s) gone from broker but no execution price yet "
                    "— holding open, will retry (%.0fs/%ds elapsed)",
                    tid, underlying, elapsed, CLOSE_GRACE_SECONDS,
                )
                continue

            await trade_recorder.record_close_unknown(
                trade_id=tid,
                exit_reason="closed_price_unavailable",
            )
            _close_pending.pop(tid, None)

        # Drop pending markers for trades that are no longer missing/open
        for tid in list(_close_pending.keys()):
            if tid not in still_missing:
                _close_pending.pop(tid, None)

        # ── Cancel the resting bracket of anything booked closed above ──────────
        # Booking the Trade row closed does nothing to the protective children
        # still working at IBKR. Every *deliberate* close cancels them first
        # (close_equity_trade, close_options_trade, close_untracked_position all
        # call broker.cancel_open_orders); this broker-detected path did not, so
        # the bracket outlived its position. Confirmed in production 2026-08-28:
        # 20 orphaned orders across ASML/EXC/INTU/MU, every one traced to an
        # exit_reason of position_closed_at_broker, while EXC's two
        # position_rotation closes on the same ticker left none. An orphan is
        # not inert — it is a live stop that, if touched, opens a brand-new
        # unintended position (EXC's was a BUY STP for 906 shares).
        #
        # Guarded, because cancelling by symbol is blunt: a symbol can hold
        # several bracket tranches, and cancelling all of them while another
        # tranche is still live would strip a real position of its stop — the
        # opposite and worse failure. So only sweep a symbol the broker no
        # longer holds AND that has no open/pending Trade row left. The DB is
        # re-read here rather than reusing all_trades because the loop above
        # has since closed rows.
        for symbol in sorted(closed_symbols):
            try:
                if symbol in live_symbols:
                    continue  # broker still holds it — its bracket is doing its job
                # func.upper, not a bare ==: `symbol` was upper-cased on the
                # way in, and a case mismatch here would return no rows and so
                # cancel a live position's stop. This comparison's failure mode
                # has to be "skip the cancel", never "cancel anyway".
                from sqlalchemy import func as _sa_func
                async with AsyncSessionLocal() as session:
                    remaining = (await session.execute(
                        select(Trade).where(
                            _sa_func.upper(Trade.underlying) == symbol,
                            Trade.status.in_(["open", "pending"]),
                        )
                    )).scalars().all()
                if remaining:
                    logger.info(
                        "Not cancelling %s orders — %d trade(s) still open/pending "
                        "on that symbol", symbol, len(remaining),
                    )
                    continue
                cancelled = await broker.cancel_open_orders(symbol)
                if cancelled:
                    logger.info(
                        "Cancelled %d orphaned order(s) for %s — no position "
                        "and no open/pending trade left", cancelled, symbol,
                    )
            except Exception as _cancel_exc:
                # Never let a failed cancel abort reconciliation — the orphan
                # survives to the next pass, which is the status quo, not worse.
                logger.error(
                    "Failed to cancel orders for %s after close: %s",
                    symbol, _cancel_exc,
                )

    except Exception as exc:
        logger.debug("_poll_fills: %s", exc)  # non-fatal


async def _sweep_orphaned_orders() -> None:
    """Cancel resting orders on symbols the broker holds no position in.

    The reconciliation loop above already cancels the bracket of anything IT
    books closed, by iterating the `closed_symbols` set it just built. That
    prevents new orphans arriving by one path and cannot see an orphan that
    already exists — from a manual close in TWS, a close booked while this
    process was down, a path predating that fix, or its own swallowed cancel
    failure ("the orphan survives to the next pass", where no later pass looks
    at it again).

    Production proved the difference. The canceller landed 2026-08-28 after 20
    orphans across ASML/EXC/INTU/MU. On 2026-09-19 there were 18, on three of
    the same tickers, still resting — roughly $176k of potential unintended
    exposure on a $100k book. An orphaned stop is not dormant: both legs of a
    short's bracket are BUYs, so with nothing to close, a touch OPENS a
    position instead of exiting one.

    Event-triggered cleanup could not fix that. This is state-triggered: it
    reads the book every reconciliation interval and acts on what is there,
    regardless of how it got there.

    Every rule inside fails toward leaving the order alone — see the module
    docstring. Failures are logged, never raised: this runs behind
    reconciliation and must not take the tick with it.
    """
    from app.broker.broker_factory import get_broker
    from app.services.orphan_order_sweep import sweep_orphaned_orders

    report = await sweep_orphaned_orders(get_broker(), dry_run=False)

    if report.get("nothing_to_do"):
        return
    if report.get("status") == "skipped":
        # Expected in normal operation (cached book, disconnected broker), so
        # debug rather than warning — except the per-pass limit, which means
        # the book looks nothing like expectations and someone should look.
        if "exceeds the" in (report.get("reason") or ""):
            logger.warning("orphan sweep refused to act: %s | %s",
                           report.get("reason"), report.get("by_symbol"))
        else:
            logger.debug("orphan sweep skipped: %s", report.get("reason"))
        return
    if report.get("status") != "ok":
        logger.warning("orphan sweep did not run: %s", report.get("reason"))
        return

    logger.warning(
        "orphan sweep: %d of %d resting order(s) with no position confirmed "
        "cancelled: %s",
        len(report.get("confirmed_cancelled") or []), report.get("requested", 0),
        report.get("by_symbol"),
    )

    if not report.get("verified"):
        # The post-cancel read fell back to cache, so nothing is confirmed.
        # Saying so beats an optimistic count: the next pass will re-check.
        logger.warning(
            "orphan sweep could not verify the cancels — the post-cancel read "
            "was not a live one. Treat this pass as unconfirmed.",
        )

    if report.get("still_open"):
        # IBKR accepted the cancel and the order is still in the book. That is
        # the 2026-08-29 failure mode, where cancelling in TWS reported nothing
        # and changed nothing. Worth an error: the exposure is still live.
        logger.error(
            "orphan sweep: %s still in the order book after cancel — the "
            "unintended-position risk is NOT cleared",
            report["still_open"],
        )

    if report.get("not_found"):
        # Already gone from the broker's book. Benign if someone else
        # cancelled it; NOT benign if it filled.
        logger.warning(
            "orphan sweep: %s were already gone from the order book — "
            "cancelled elsewhere, or filled",
            report["not_found"],
        )

    if report.get("possible_fills"):
        # A not_found order whose symbol now HAS a position. That is the
        # unintended position opening — the exact outcome this sweep exists to
        # prevent — so it is an error, not a footnote.
        logger.error(
            "orphan sweep: %s now hold a position after an orphaned order went "
            "missing — an orphan may have FILLED and opened an unintended "
            "position. Check the book by hand.",
            report["possible_fills"],
        )

    if report.get("errored"):
        logger.error("orphan sweep: broker refused to cancel %s",
                     report["errored"])


async def _update_portfolio_greeks() -> None:
    global _greeks_tracker
    if _greeks_tracker is None:
        return
    try:
        from app.broker.broker_factory import get_broker
        from app.services.trade_excursion_tracker import trade_excursion_tracker
        broker = get_broker()
        positions = await broker.get_positions()

        excursion_result = await trade_excursion_tracker.update_from_broker_positions(positions)
        if excursion_result.updated_trades > 0:
            logger.debug(
                "Trade excursion tracker updated %d trade(s)",
                excursion_result.updated_trades,
            )

        # Rebuild the tracker from live positions on every tick
        _greeks_tracker._positions.clear()
        for pos in positions:
            # Equities carry a placeholder option_type="call" and strike=0 from the
            # broker client, so option_type alone misclassifies them as options
            # (with zero Greeks). Use the strike==0 sentinel to detect equities.
            is_option = bool(getattr(pos, "option_type", None)) and float(getattr(pos, "strike", 0) or 0) != 0
            if is_option:
                # Options position — use broker-supplied Greeks if available
                greeks = getattr(pos, "greeks", None)
                _greeks_tracker.add_options_position(
                    symbol=pos.symbol,
                    qty=abs(pos.quantity),
                    side="short" if pos.quantity < 0 else "long",
                    delta=float(greeks.delta) if greeks else 0.0,
                    vega=float(greeks.vega) if greeks else 0.0,
                    theta=float(greeks.theta) if greeks else 0.0,
                )
            else:
                # Equity position
                _greeks_tracker.add_equity_position(
                    ticker=getattr(pos, "symbol", getattr(pos, "underlying", "?")),
                    qty=abs(pos.quantity),
                    side="long" if pos.quantity > 0 else "short",
                )

        snap = _greeks_tracker.snapshot()
        logger.debug(
            "Portfolio Greeks: delta=%+.4f vega=%+.4f theta=%+.4f positions=%d",
            snap["net_delta"], snap["net_vega"], snap["net_theta"], snap["total_position_count"],
        )
    except Exception as exc:
        logger.warning("Greeks update failed: %s", exc)


async def _check_signal_outcomes() -> None:
    """Resolve pending signal outcomes against fresh daily bars.

    The inner deadline sits below the _guarded() budget on purpose. These rows
    are training labels, so a pass that stops early must stop *cleanly* — at a
    ticker boundary, with its writes flushed and its shortfall reported. Being
    cancelled by the outer guard instead leaves a silently partial label set,
    which is what happened for the whole of 2026-08-27/28.
    """
    from app.services.signal_outcome_tracker import check_pending_outcomes
    summary = await check_pending_outcomes(
        deadline_seconds=SIGNAL_OUTCOMES_DEADLINE_S,
    )
    if summary["checked"] > 0:
        logger.info(
            "Signal outcomes: checked=%d target_hit=%d stop_hit=%d expired=%d "
            "still_pending=%d coverage=%d/%d truncated=%s elapsed=%.1fs",
            summary["checked"], summary["target_hit"], summary["stop_hit"],
            summary["expired"], summary["still_pending"],
            summary["tickers_covered"], summary["tickers_total"],
            summary["truncated"], summary["elapsed_s"],
        )

    # Second pass: fill in full-window excursions for rows that resolved before
    # enough bars existed to measure one. The pass above writes a resolution the
    # moment a barrier is touched — right for the operator, but it means a
    # signal that resolved on day 2 was measured over two bars and would be
    # rejected as incomplete forever, because nothing selects a resolved row
    # again. Without this, uncensored MFE never accumulates and the censoring
    # ceiling never lifts.
    #
    # Its own budget, and failures are swallowed: this is an enrichment of data
    # that is already correct, so it must never take the outcome pass down with
    # it.
    try:
        from app.services.signal_outcome_tracker import enrich_incomplete_excursions
        enriched = await enrich_incomplete_excursions(
            budget_s=SIGNAL_OUTCOMES_DEADLINE_S,
        )
        if enriched["enriched"] > 0:
            logger.info(
                "Excursion enrichment: candidates=%d enriched=%d completed=%d "
                "tickers=%d truncated=%s elapsed=%.1fs",
                enriched["candidates"], enriched["enriched"],
                enriched["completed"], enriched["tickers_covered"],
                enriched["truncated"], enriched["elapsed_s"],
            )
    except Exception as exc:
        logger.warning("Excursion enrichment failed (outcomes are unaffected): %s", exc)


async def _reconcile_positions() -> None:
    """Periodic broker/DB reconciliation — position_reconciler.py's own
    docstring describes it as running "on every startup and before every
    signal cycle," but nothing previously called it automatically; it was
    only reachable via an on-demand API route. Alert-only: logs critically
    and records an observability event/counter on a real mismatch (visible
    in /api/health/detail) rather than auto-engaging the kill switch — no
    existing code in this app auto-halts trading from a background check,
    and a false positive during a normal fill-timing window would halt
    trading for no real reason. check() only reads get_positions(), which
    is a local ib_insync cache read (see ibkr_client.py), so this is cheap
    enough to run every cycle without IBKR request-coordinator involvement.
    """
    from app.broker.broker_factory import get_broker
    from app.services.observability import observability
    from app.services.position_reconciler import PositionReconciler

    result = await PositionReconciler(get_broker()).check()
    if not result.clean:
        logger.critical(
            "Position reconciliation mismatch: untracked_at_broker=%s phantom_in_db=%s warnings=%s",
            result.untracked_at_broker, result.phantom_in_db, result.warnings,
        )
        observability.incr("reconciliation.mismatch")
        observability.event(
            "reconciliation_mismatch",
            untracked_at_broker=result.untracked_at_broker,
            phantom_in_db=result.phantom_in_db,
            warnings=result.warnings,
        )
        if result.untracked_at_broker:
            from app.core.config import settings
            if getattr(settings, "reconciliation_auto_adopt_untracked", True):
                await _adopt_untracked_positions(result.untracked_at_broker)
        if result.quantity_mismatch_tickers:
            from app.core.config import settings
            if getattr(settings, "reconciliation_auto_correct_quantity", True):
                await _correct_quantity_mismatches(result.quantity_mismatch_tickers)


async def _correct_quantity_mismatches(mismatched: list[str]) -> None:
    """
    Correct a tracked Trade row's quantity when it disagrees with the
    broker's live quantity for that underlying — see
    reconciliation_auto_correct_quantity's config docstring for why this
    exists (2026-08-26: MRVL/SNDK rows still held stale quantities from
    before the close_equity_trade() sizing bug was fixed in commit
    1cc3eb8 — that fix stops new mismatches, it never retroactively
    corrected rows it had already damaged).

    The broker is always the source of truth (position_reconciler.py's own
    design principle). Equity only, and only when there is exactly one
    open equity Trade row for the ticker — with zero or more than one open
    row it's ambiguous which one the broker's single summed quantity
    belongs to, so it's skipped and logged for manual review rather than
    guessed at. Only quantity is touched; entry price/P&L fields are left
    alone since this corrects share count, not cost basis.
    """
    from sqlalchemy import select

    from app.broker.broker_factory import get_broker
    from app.core.database import AsyncSessionLocal
    from app.models.trade import Trade
    from app.services.observability import observability

    try:
        positions = await get_broker().get_equity_positions()
    except Exception as exc:
        logger.error("correct-quantity-mismatch: could not fetch live equity positions: %s", exc)
        return

    live_qty = {p.symbol.upper(): abs(int(p.quantity)) for p in positions if p.quantity}

    corrected: list[str] = []
    async with AsyncSessionLocal() as session:
        async with session.begin():
            for ticker in mismatched:
                symbol = ticker.upper()
                if symbol not in live_qty:
                    continue  # not a live equity position — likely an options mismatch, out of scope

                rows = (await session.execute(
                    select(Trade).where(
                        Trade.underlying == symbol,
                        Trade.status == "open",
                    )
                )).scalars().all()
                equity_rows = [t for t in rows if (t.spread_type or "").lower().startswith("equity")]
                if len(equity_rows) != 1:
                    logger.warning(
                        "correct-quantity-mismatch: %s has %d open equity Trade row(s) "
                        "(expected exactly 1) — skipping auto-correct, manual review required",
                        symbol, len(equity_rows),
                    )
                    continue

                trade = equity_rows[0]
                old_qty = trade.quantity
                trade.quantity = live_qty[symbol]
                corrected.append(symbol)
                logger.critical(
                    "Corrected quantity mismatch for %s: db=%s -> broker=%s (trade_id=%s)",
                    symbol, old_qty, live_qty[symbol], trade.id,
                )

    if corrected:
        observability.incr("reconciliation.quantity_corrected")
        observability.event("reconciliation_quantity_corrected", tickers=corrected)


async def _adopt_untracked_positions(untracked: list[str]) -> None:
    """
    Write a Trade row for each live broker equity position that has none —
    see reconciliation_auto_adopt_untracked's config docstring for why this
    exists (2026-08-26 incident: untracked positions were invisible to
    every DB-derived guardrail).

    Equity only. An untracked position that doesn't show up in
    get_equity_positions() is assumed to be an options position — closing
    an options spread requires knowing its paired short/long strikes, which
    can't be reconstructed from a reconciliation mismatch alone; it's
    logged for manual review instead of guessed at.

    Re-checks open Trade rows under the same DB session immediately before
    inserting, so this can't race a legitimate concurrent entry that closed
    the gap between the reconciler's read and this write.

    The adopted row's entry_date/short_strike/long_strike are discovery-time
    placeholders, not the position's real historical entry — this is a
    known, accepted limitation (logged clearly below), not a claim of
    historical accuracy. credit_received is set to the live avg_cost
    specifically because position_risk_dollars() (portfolio_engine.py)
    reads credit_received×quantity as an equity trade's risk dollars — this
    is the field that makes the adopted row actually count for guardrails.
    """
    from datetime import date, datetime, timezone
    from decimal import Decimal

    from sqlalchemy import select

    from app.broker.broker_factory import get_broker
    from app.core.database import AsyncSessionLocal
    from app.models.trade import Trade
    from app.services.observability import observability

    try:
        positions = await get_broker().get_equity_positions()
    except Exception as exc:
        logger.error("adopt-untracked: could not fetch live equity positions: %s", exc)
        return

    by_symbol = {p.symbol.upper(): p for p in positions if p.quantity}

    adopted: list[str] = []
    async with AsyncSessionLocal() as session:
        async with session.begin():
            open_trades = (
                await session.execute(select(Trade).where(Trade.status == "open"))
            ).scalars().all()
            already_tracked = {str(t.underlying).upper() for t in open_trades}

            for ticker in untracked:
                symbol = ticker.upper()
                if symbol in already_tracked:
                    continue  # closed by a legitimate concurrent entry since the check ran

                pos = by_symbol.get(symbol)
                if pos is None:
                    logger.warning(
                        "adopt-untracked: %s not found among live equity positions "
                        "(likely an untracked OPTIONS position) — skipping auto-adopt, "
                        "manual review required",
                        symbol,
                    )
                    continue

                price = Decimal(str(pos.avg_cost))
                session.add(Trade(
                    strategy="adopted_untracked",
                    underlying=symbol,
                    spread_type="equity_long" if pos.quantity > 0 else "equity_short",
                    short_strike=price,
                    long_strike=price,
                    expiration=date.today(),
                    entry_date=datetime.now(timezone.utc),
                    credit_received=price,
                    mfe_pnl=Decimal("0"),
                    mae_pnl=Decimal("0"),
                    status="open",
                    quantity=abs(int(pos.quantity)),
                    approved_by="reconciler_adopt",
                ))
                adopted.append(symbol)

    if adopted:
        logger.critical(
            "Auto-adopted %d untracked broker position(s) into DB as Trade rows: %s "
            "— entry_date/strikes are discovery-time placeholders, not real "
            "historical entry data. strategy=adopted_untracked.",
            len(adopted), adopted,
        )
        observability.incr("reconciliation.auto_adopted")
        observability.event("reconciliation_auto_adopted", tickers=adopted)


async def _refresh_rotation_correlation_cache() -> None:
    """Periodic refresh of the rotation-scoped correlation cache — see
    rotation_correlation_cache.py. Deferred from Phase 3 Increment 1
    (Winner Protection) so rotate_for_blocked_entry() never itself
    triggers a live yfinance fetch."""
    from app.services.rotation_correlation_cache import refresh
    await refresh()


async def _capture_daily_snapshot() -> None:
    """Freeze the day's top 3 BUY / top 3 SELL at 10:00 ET — see
    daily_signal_snapshot.py.

    The fixed time is the whole point. The scanner re-scores the same signal
    ~45 times a session, so a top-3 computed later would pick each signal's
    best-scoring moment, including ones that only surfaced after the move.
    Freezing makes the row mean "this is what was on screen when you could
    have acted" rather than "this is what looked good in hindsight".

    Idempotent: a date that already has rows is never rewritten.
    """
    from app.services.daily_signal_snapshot import capture_daily_snapshot
    from app.utils.market_hours import now_et

    et = now_et()
    if et.weekday() >= 5:            # weekend — no session to snapshot
        return
    if et.hour != 10:                # the scheduler tick may drift; the hour is the gate
        return

    report = await capture_daily_snapshot()
    if report.get("status") == "already_captured":
        return
    if report.get("status") == "no_scored_signals":
        logger.info("daily snapshot %s: no scored signals to freeze",
                    report.get("trade_date"))
        return
    logger.info("daily snapshot %s: froze %d — buy %s / sell %s",
                report.get("trade_date"), report.get("captured"),
                report.get("buy"), report.get("sell"))


async def _refresh_sector_cache() -> None:
    """Resolve ticker -> sector for the watchlist — see sector_cache.py.

    Sector membership is static data, so this runs daily rather than on a
    trading cadence. Until it lands, unclassified tickers simply do not
    participate in the sector cap, which is the same direction an unresolved
    ticker fails in anyway.
    """
    from app.services.sector_cache import refresh
    await refresh()


async def _backfill_equity_stops() -> None:
    """Recover stops for positions adopted without one — see stop_backfill.py.

    Runs on the scheduler because _adopt_untracked_positions() keeps creating
    the gap: it has no entry plan to copy a stop from, so it writes placeholder
    prices, and until the real stop is recovered those positions report full
    notional to portfolio heat and both concentration checks. Doing it once by
    hand would fix today's rows and leave tomorrow's broken.

    Deliberately paired with reconciliation's own interval — the reconciler is
    what creates the stopless row, so this is the cleanup that belongs behind
    it. Its own first step is a DB read that returns early when every position
    already has a stop, so the steady state costs the broker nothing.

    Writes at most one column on open equity rows and submits no orders. Every
    rule inside fails closed to leaving the row alone.
    """
    from app.broker.broker_factory import get_broker
    from app.services.stop_backfill import backfill_equity_stops

    report = await backfill_equity_stops(get_broker(), dry_run=False)
    if report.get("nothing_to_do"):
        return
    if report.get("status") != "ok":
        logger.warning("stop backfill did not run: %s", report.get("reason"))
        return
    if report.get("updated"):
        logger.info(
            "stop backfill recorded %d stop(s): %s",
            len(report["updated"]),
            ", ".join(f"{r['ticker']}@{r['stop_price']}" for r in report["updated"]),
        )


# ── Health check ────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
async def health_check() -> dict[str, str]:
    """Returns 200 OK when the service is up."""
    return {"status": "ok", "broker": settings.broker}


@app.get("/api/health/detail", tags=["System"])
async def health_detail() -> dict:
    """
    Lightweight operational snapshot: scanner heartbeat, kill-switch state, the
    current regime, database connectivity, and the in-process observability
    counters / recent events. The database check runs a trivial SELECT 1 and
    never raises — an outage is reported in the response, not a 500.
    """
    import time as _t
    from app.services.observability import observability
    from app.services.kill_switch import kill_switch_service as _ks
    from app.broker.broker_factory import get_broker
    from app.broker.ibkr_coordinator import ibkr_coordinator
    from app.services import options_chain_cache

    age = (_t.monotonic() - _scheduler_last_tick) if _scheduler_last_tick else None
    scanner_ok = age is not None and age < 90

    _broker = get_broker()
    _ib_connected = bool(getattr(_broker, "_connected", False))
    if hasattr(_broker, "ib"):
        _ib_connected = _ib_connected and _broker.ib.isConnected()

    # Which IBKR account is this process actually logged into? Added 2026-08-27
    # after a position-count disagreement (app reported 9 open, the operator's
    # brokerage view showed 6) survived a full backend restart — ruling out a
    # stale local cache and leaving "the gateway is on a different account than
    # the one being looked at" as a live hypothesis that nothing in the logs or
    # any route could confirm. ib.managedAccounts() is a plain local read of
    # the account list IBKR pushes at connect time — no network round-trip, no
    # coordinator, nothing to time out. Masked to the last 4 characters because
    # this endpoint is unauthenticated; the last 4 are enough to compare
    # against a brokerage view without publishing the full account number.
    _accounts: list[str] = []
    try:
        for _acct in (_broker.ib.managedAccounts() or []) if hasattr(_broker, "ib") else []:
            _acct = str(_acct).strip()
            if _acct:
                _accounts.append(f"****{_acct[-4:]}" if len(_acct) > 4 else "****")
    except Exception:
        _accounts = []

    # Does the account-values cache hold anything despite the subscribe
    # timing out? get_account_summary() reads ib.accountValues() *after*
    # awaiting the subscribe, so when the subscribe raises we never find
    # out whether the data was there all along. If this is non-zero the
    # subscribe may be unnecessary and the blackout is a control-flow bug,
    # not a data problem. Local read of an already-populated list — no
    # network round-trip, same as managedAccounts() above.
    _account_value_count = 0
    _account_value_tags: list[str] = []
    try:
        _vals = (_broker.ib.accountValues() or []) if hasattr(_broker, "ib") else []
        _account_value_count = len(_vals)
        _account_value_tags = sorted({
            str(getattr(v, "tag", "")) for v in _vals
            if str(getattr(v, "tag", "")) in (
                "NetLiquidation", "TotalCashValue", "BuyingPower",
                "MaintMarginReq", "ExcessLiquidity",
            )
        })
    except Exception:
        _account_value_count = -1  # -1 distinguishes "read failed" from "empty"

    # Age of that cache. A populated cache says nothing about whether TWS is
    # still pushing into it, so the count above cannot distinguish live data
    # from data frozen at the moment the stream died — this is the field that
    # can. None means nothing has ever arrived on this connection.
    _account_values_age = None
    _account_values_stale = False
    try:
        if hasattr(_broker, "account_values_age_seconds"):
            _age_raw = _broker.account_values_age_seconds()
            _account_values_age = round(_age_raw, 1) if _age_raw is not None else None
            _account_values_stale = _broker.account_values_are_stale()
    except Exception:
        pass

    _db_connected = True
    try:
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import text
        async with AsyncSessionLocal() as _s:
            await _s.execute(text("SELECT 1"))
    except Exception:
        _db_connected = False

    # What is actually scoring signals. Reuses the already-constructed scorer
    # when the scan loop has built one; otherwise constructs a throwaway purely
    # to read model state — cheap when no model file is present, which is
    # precisely the case this block exists to make visible.
    try:
        from app.services.signal_scorer import SignalScorer
        _scorer = _signal_scorer if _signal_scorer is not None else SignalScorer()
        _model_status = _scorer.status()
    except Exception as _ms_exc:
        _model_status = {"error": f"{type(_ms_exc).__name__}: {_ms_exc}"}

    return {
        "status": "ok",
        "broker": settings.broker,
        "scanner": {
            "alive": scanner_ok,
            "last_tick_age_seconds": round(age, 1) if age is not None else None,
        },
        "kill_switch": {"engaged": _ks.is_engaged, "reason": _ks.status.get("reason")},
        "regime": getattr(getattr(_current_regime, "regime", None), "value", None),
        "database": {"connected": _db_connected},
        "signal_model": _model_status,
        "observability": observability.snapshot(),
        "ibkr": {
            "connected": _ib_connected,
            "trading_mode": settings.ibkr_trading_mode,
            # Masked (last 4) account identifiers this process is logged into.
            # More than one entry means the login has access to multiple
            # accounts, in which case position reads may not be scoped to the
            # account being compared against — see the block above.
            "accounts": _accounts,
            "account_count": len(_accounts),
            "account_values_cached": _account_value_count,
            "account_value_tags_present": _account_value_tags,
            "account_values_age_seconds": _account_values_age,
            "account_values_stale": _account_values_stale,
            "coordinator": ibkr_coordinator.health_snapshot(),
            "options_chain_cache": options_chain_cache.stats(),
        },
    }


# ── Guardrail endpoints ─────────────────────────────────────────────────────
from app.services.guardrails import GuardrailEngine, GuardrailStatus, PortfolioState

_guardrail_engine = GuardrailEngine()
# In-memory dedup signature for guardrail event logging — None means "not yet
# seeded from DB this process", so a restart doesn't re-log the unchanged
# current state as if it were a fresh transition. See _maybe_log_guardrail_event.
_last_guardrail_signature: tuple | None = None
# In-memory cache of the persisted portfolio peak value — None means "not yet
# seeded from DB this process". See _get_or_update_peak_value.
_peak_value_cache: float | None = None


async def _get_or_update_peak_value(current_value: float, starting_capital: float) -> float:
    """
    Returns the persisted all-time portfolio peak (risk_peak_state, row
    id=1) for the Drawdown Control guardrail, updating it in place if
    current_value is a new high. Seeds once per process from DB — first
    ever call initializes the row to max(current_value, starting_capital).
    Never raises: a persistence hiccup must not break the status response
    trading decisions depend on (same contract as _maybe_log_guardrail_event).
    """
    global _peak_value_cache
    from app.core.database import AsyncSessionLocal
    from app.models.risk_state import RiskPeakState

    try:
        async with AsyncSessionLocal() as session:
            row = None
            if _peak_value_cache is None:
                row = await session.get(RiskPeakState, 1)
                if row is None:
                    row = RiskPeakState(id=1, peak_value=Decimal(str(max(current_value, starting_capital))))
                    session.add(row)
                    await session.commit()
                _peak_value_cache = float(row.peak_value)
            if current_value > _peak_value_cache:
                if row is None:
                    row = await session.get(RiskPeakState, 1)
                row.peak_value = Decimal(str(current_value))
                await session.commit()
                _peak_value_cache = current_value
        return _peak_value_cache
    except Exception as exc:
        logger.error("Failed to update peak value: %s", exc)
        # Do NOT cache a fallback here — that would permanently stop this
        # process from ever touching the DB again (a transient failure,
        # e.g. mid-deploy before the migration lands, would silently poison
        # every guardrail check for the rest of the process's life). Return
        # an uncached value for just this one response instead.
        return _peak_value_cache if _peak_value_cache is not None else max(current_value, starting_capital)


async def _maybe_log_guardrail_event(status: GuardrailStatus, portfolio: PortfolioState) -> None:
    """
    Persist a GuardrailEvent row only when the guardrail state actually
    transitions (entering or recovering from a restricted mode) — not on
    every 15s poll of /api/guardrails/status. Logs both directions so
    history reads as a timeline, not a one-way ratchet. Never raises: a
    persistence hiccup must not break the status response trading
    decisions depend on.
    """
    global _last_guardrail_signature
    from app.core.database import AsyncSessionLocal
    from app.models.risk_state import GuardrailEvent
    from sqlalchemy import select

    signature = (status.trading_mode, status.flags[0] if status.flags else None)
    # Reverse map from a persisted event_type back to the (trading_mode, flag)
    # signature it represents — needed to seed the in-memory cache from DB
    # history in the SAME shape `signature` above uses, not just the raw
    # event_type string (those aren't the same string space: trading_mode is
    # "normal"/"capital_preservation"/"suspended", event_type is the specific
    # rule/flag name). kill_switch/kill_switch_reset aren't part of
    # check_all()'s vocabulary at all — a kill-switch event as the most
    # recent row doesn't tell us anything about the last known guardrail
    # signature, so it's skipped when seeding.
    _SUSPEND_EVENT_TYPES = {
        "cooling_off_active", "daily_loss_limit", "weekly_loss_limit",
        "monthly_loss_limit", "max_drawdown_limit", "consecutive_loss_limit",
    }
    try:
        async with AsyncSessionLocal() as session:
            if _last_guardrail_signature is None:
                last = (await session.execute(
                    select(GuardrailEvent)
                    .where(GuardrailEvent.event_type.notin_(("kill_switch", "kill_switch_reset")))
                    .order_by(GuardrailEvent.timestamp.desc()).limit(1)
                )).scalars().first()
                if last is None or last.event_type == "normal":
                    _last_guardrail_signature = ("normal", None)
                elif last.event_type == "capital_preservation_mode":
                    _last_guardrail_signature = ("capital_preservation", last.event_type)
                elif last.event_type in _SUSPEND_EVENT_TYPES:
                    _last_guardrail_signature = ("suspended", last.event_type)
                else:
                    _last_guardrail_signature = ("normal", last.event_type)
            if signature == _last_guardrail_signature:
                return
            event_type = signature[1] or "normal"
            # The trigger/limit pair genuinely relevant to *this* rule — not a
            # single field reused for every event type regardless of which
            # rule actually fired.
            trigger_by_type = {
                "daily_loss_limit":          status.daily_loss_pct,
                "weekly_loss_limit":         status.weekly_loss_pct,
                "monthly_loss_limit":        status.monthly_loss_pct,
                "max_drawdown_limit":        status.drawdown_pct,
                "consecutive_loss_limit":    status.consecutive_losses,
                "daily_trade_cap":           status.trades_today,
                "capital_preservation_mode": status.capital_pct_remaining,
            }
            limit_by_type = {
                "daily_loss_limit":          -_guardrail_engine.max_daily_loss_pct,
                "weekly_loss_limit":         -_guardrail_engine.max_weekly_loss_pct,
                "monthly_loss_limit":        -_guardrail_engine.max_monthly_loss_pct,
                "max_drawdown_limit":        _guardrail_engine.max_drawdown_pct,
                "consecutive_loss_limit":    _guardrail_engine.max_consecutive_losses,
                "daily_trade_cap":           _guardrail_engine.max_trades_per_day,
                "capital_preservation_mode": _guardrail_engine.preservation_threshold,
            }
            trigger = trigger_by_type.get(event_type, status.capital_pct_remaining)
            limit = limit_by_type.get(event_type)
            session.add(GuardrailEvent(
                event_type=event_type,
                trigger_value=Decimal(str(trigger)),
                limit_value=Decimal(str(limit)) if limit is not None else None,
                trading_suspended_until=status.suspended_until,
                portfolio_value=Decimal(str(portfolio.current_value)),
                notes=status.reason or f"trading_mode → {status.trading_mode}",
            ))
            await session.commit()
            _last_guardrail_signature = signature
    except Exception as exc:
        logger.error("Failed to persist guardrail event: %s", exc)


# docker-compose's healthcheck for the whole backend container hits this
# route (interval 30s, timeout 10s) — kept well under that so a slow/stuck
# IBKR connection can't hang the healthcheck and mark the container
# unhealthy. Module-level so tests can patch it down for speed.
ACCOUNT_SUMMARY_ROUTE_TIMEOUT_SECONDS = 5.0


@app.get("/api/guardrails/status", tags=["Guardrails"])
async def guardrail_status():
    from app.core.database import AsyncSessionLocal
    from app.models.trade import Trade
    from sqlalchemy import select, func, and_
    from datetime import date, timedelta

    # Read real P&L from DB
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    try:
        async with AsyncSessionLocal() as session:
            def _pnl_window(from_date):
                return select(func.coalesce(func.sum(Trade.pnl), 0)).where(
                    and_(Trade.status == "closed", func.date(Trade.exit_date) >= from_date)
                )
            daily_pnl   = float((await session.execute(_pnl_window(today))).scalar() or 0)
            weekly_pnl  = float((await session.execute(_pnl_window(week_start))).scalar() or 0)
            monthly_pnl = float((await session.execute(_pnl_window(month_start))).scalar() or 0)
            # Count trades ENTERED today (matches the daily-cap semantic used in
            # trade_desk._fetch_portfolio_state), regardless of open/closed status.
            trades_today = int((await session.execute(
                select(func.count(Trade.id)).where(
                    func.date(Trade.entry_date) == today
                )
            )).scalar() or 0)
            recent = (await session.execute(
                select(Trade.pnl).where(Trade.status == "closed")
                .order_by(Trade.exit_date.desc()).limit(10)
            )).scalars().all()
            consecutive_losses = 0
            for p in recent:
                if (p or 0) < 0: consecutive_losses += 1
                else: break
    except Exception:
        daily_pnl = weekly_pnl = monthly_pnl = 0.0
        trades_today = consecutive_losses = 0

    # Get real broker account value. get_account_summary() has no timeout of
    # its own, so a slow or stuck IBKR connection would otherwise hang this
    # endpoint (and every healthcheck probe with it — see the timeout
    # constant above) indefinitely.
    try:
        import asyncio as _asyncio
        from app.broker.broker_factory import get_broker
        acct = await _asyncio.wait_for(
            get_broker().get_account_summary(),
            timeout=ACCOUNT_SUMMARY_ROUTE_TIMEOUT_SECONDS,
        )
        current_value = float(acct.net_liquidation)
    except Exception:
        current_value = settings.starting_capital

    peak_value = await _get_or_update_peak_value(current_value, settings.starting_capital)

    portfolio = PortfolioState(
        current_value=current_value,
        starting_capital=settings.starting_capital,
        daily_pnl=daily_pnl,
        weekly_pnl=weekly_pnl,
        monthly_pnl=monthly_pnl,
        consecutive_losses=consecutive_losses,
        trades_today=trades_today,
        peak_value=peak_value,
    )
    status = _guardrail_engine.check_all(portfolio)
    await _maybe_log_guardrail_event(status, portfolio)

    return {
        "trading_allowed":       status.trading_allowed,
        "trading_mode":          status.trading_mode,
        "reason":                status.reason,
        "flags":                 status.flags,
        "paper_mode":            settings.is_paper_trading,
        "paper_visibility_mode": settings.paper_visibility_active,
        # Display-only OMS flag for status lamps (not a control surface).
        "position_rotation_on_max": bool(
            getattr(settings, "position_rotation_on_max", False)
        ),
        "daily_pnl":             daily_pnl,
        "weekly_pnl":            weekly_pnl,
        "monthly_pnl":           monthly_pnl,
        "daily_loss_pct":        status.daily_loss_pct,
        "weekly_loss_pct":       status.weekly_loss_pct,
        "monthly_loss_pct":      status.monthly_loss_pct,
        "drawdown_pct":          status.drawdown_pct,
        "consecutive_losses":    consecutive_losses,
        "trades_today":          trades_today,
        "capital_pct_remaining": status.capital_pct_remaining,
        "max_daily_loss_pct":    _guardrail_engine.max_daily_loss_pct,
        "max_weekly_loss_pct":   _guardrail_engine.max_weekly_loss_pct,
        "max_monthly_loss_pct":  _guardrail_engine.max_monthly_loss_pct,
        "max_drawdown_pct":      _guardrail_engine.max_drawdown_pct,
        "max_trades_per_day":    _guardrail_engine.max_trades_per_day,
        "max_consecutive_losses": _guardrail_engine.max_consecutive_losses,
        "capital_preservation_threshold": _guardrail_engine.preservation_threshold,
        "signal_threshold":      _guardrail_engine.get_signal_threshold(status),
    }


@app.get("/api/guardrails/history", tags=["Guardrails"])
async def guardrail_history(limit: int = 50):
    from app.core.database import AsyncSessionLocal
    from app.models.risk_state import GuardrailEvent
    from sqlalchemy import select
    try:
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                select(GuardrailEvent).order_by(GuardrailEvent.timestamp.desc()).limit(limit)
            )).scalars().all()
    except Exception:
        return {"events": []}
    return {"events": [
        {
            "id": str(r.id),
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "event_type": r.event_type,
            "trigger_value": float(r.trigger_value) if r.trigger_value is not None else None,
            "limit_value": float(r.limit_value) if r.limit_value is not None else None,
            "trading_suspended_until": r.trading_suspended_until.isoformat() if r.trading_suspended_until else None,
            "portfolio_value": float(r.portfolio_value) if r.portfolio_value is not None else None,
            "notes": r.notes,
        }
        for r in rows
    ]}


@app.get("/api/guardrails/trading-mode", tags=["Guardrails"])
async def get_trading_mode():
    from app.services.trading_mode import trading_mode_manager
    return {"mode": trading_mode_manager.current.active_mode.value, "signal_threshold": settings.signal_score_threshold}


# ── Executive Summary (Dashboard header) ────────────────────────────────────
@app.get("/api/dashboard/summary", tags=["Dashboard"])
async def dashboard_summary():
    """
    Aggregated header for the Executive Summary panel: total equity, total P&L,
    day P&L, and a System Health checklist (broker, DB, agent, market hours,
    kill switch, config) with an issue count.
    """
    import time as _t
    from datetime import date as _date
    from sqlalchemy import select, func, and_
    from app.core.database import AsyncSessionLocal
    from app.models.trade import Trade
    from app.services.execution_mode import execution_mode_manager
    from app.services.kill_switch import kill_switch_service
    from app.utils.market_hours import market_status

    cap = float(settings.starting_capital)

    # ── P&L from DB (known-pnl closed trades only) ──────────────────────────
    total_pnl = day_pnl = 0.0
    db_ok = True
    try:
        today = _date.today()
        async with AsyncSessionLocal() as s:
            total_pnl = float((await s.execute(
                select(func.coalesce(func.sum(Trade.pnl), 0)).where(
                    Trade.status == "closed", Trade.pnl.isnot(None))
            )).scalar() or 0)
            day_pnl = float((await s.execute(
                select(func.coalesce(func.sum(Trade.pnl), 0)).where(
                    and_(Trade.status == "closed", Trade.pnl.isnot(None),
                         func.date(Trade.exit_date) == today))
            )).scalar() or 0)
    except Exception:
        db_ok = False

    # ── Equity from broker — no synthetic fallback ──────────────────────────
    # This used to default to `cap + total_pnl` and only overwrite on success,
    # so a failed broker read produced a plausible, authoritative-looking
    # number that was pure arithmetic. Confirmed live 2026-08-27: the account
    # read failed 100% of the day and the dashboard showed $245,494.81
    # (250,000 starting capital + -4,505.19 DB P&L) while IBKR actually held
    # $254,029.86 — an $8.5k gap presented with no staleness indicator,
    # because the timeout also surfaced as an empty broker_error string.
    # Unknown must read as unknown: None here renders as "—" in the UI.
    total_equity: Optional[float] = None
    broker_ok = False
    broker_detail = "Disconnected"
    try:
        from app.broker.broker_factory import get_broker
        broker = get_broker()
        ib = getattr(broker, "ib", None)
        broker_ok = bool(getattr(broker, "_connected", False)) and bool(ib.isConnected()) if ib else False
        if broker_ok:
            from app.broker.ibkr_coordinator import Priority, ibkr_coordinator
            acct = await ibkr_coordinator.submit(
                Priority.P0, broker.get_account_summary, key="account_summary", req_type="ACCOUNT_SUMMARY",
                timeout=ACCOUNT_SUMMARY_ROUTE_TIMEOUT_SECONDS,
            )
            nl = float(acct.net_liquidation or 0)
            if nl > 0:
                total_equity = nl
            paper = settings.ibkr_trading_mode.lower() != "live"
            broker_detail = f"Connected — account {'PAPER' if paper else 'LIVE'}"
    except Exception:
        broker_ok = False

    # ── Background agent heartbeat ──────────────────────────────────────────
    agent_ok = bool(_scheduler_last_tick) and (_t.monotonic() - _scheduler_last_tick) < 90
    exec_mode = execution_mode_manager.mode.value

    mkt = market_status()
    ks_engaged = kill_switch_service.is_engaged
    gate_on = getattr(settings, "market_hours_only", True)

    health = [
        {"name": "IBKR Gateway", "status": "ok" if broker_ok else "error",
         "detail": broker_detail},
        {"name": "PostgreSQL", "status": "ok" if db_ok else "error",
         "detail": "Connected" if db_ok else "Unreachable"},
        {"name": "Trading Agent", "status": "ok" if agent_ok else "warn",
         "detail": f"Running — {exec_mode}" if agent_ok else "Not ticking"},
        {"name": "Market Hours", "status": "ok" if mkt["is_open"] else "warn",
         "detail": "Market open" if mkt["is_open"] else f"Market closed ({mkt['reason']})"},
        {"name": "Kill Switch",
         "status": "error" if ks_engaged else "ok",
         "detail": "ENGAGED — trading halted" if ks_engaged else "Disarmed"},
        {"name": "Config",
         "status": "warn" if settings.execution_test_mode else "ok",
         "detail": (("⚠ TEST MODE (guards bypassed) · " if settings.execution_test_mode else "")
                    + f"Mode {exec_mode} · market-hours gate {'on' if gate_on else 'off'} "
                    + f"· RoR threshold {settings.signal_score_threshold}")},
    ]
    issues = sum(1 for h in health if h["status"] != "ok")

    return {
        # None (not a number) when the broker read failed — the UI renders
        # "—" rather than asserting a value it does not have.
        "total_equity":  round(total_equity, 2) if total_equity is not None else None,
        "total_equity_source": "broker" if total_equity is not None else "unavailable",
        "total_pnl":     round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl / cap * 100, 2) if cap else 0.0,
        "day_pnl":       round(day_pnl, 2),
        "day_pnl_pct":   round(day_pnl / cap * 100, 2) if cap else 0.0,
        "health":        health,
        "issues":        issues,
    }
