"""
Paper trading routes — wired to live broker positions + DB trade history.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select, func, and_, case

from app.broker.broker_factory import get_broker
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.trade import Trade
from app.services.trade_identity import asset_class_from_trade, position_identity_key

router = APIRouter()

# get_account_summary() has no timeout of its own; this route is polled by
# the frontend every 30s (usePaperTrade.ts).
ACCOUNT_SUMMARY_TIMEOUT_SECONDS = 5.0


def _hold_days_utc(entry: Optional[datetime]) -> Optional[int]:
    """Calendar days a position has been held, computed entirely in UTC.

    Trade.entry_date is DateTime(timezone=True) — tz-aware UTC. This used to
    be measured against date.today(), which returns the *system-local* date,
    so the answer was one short for part of every day on any host west of
    UTC. Caught by a test asserting a 5-day hold: it read 4 on a UTC-7
    machine while passing in CI at UTC, i.e. the same code disagreeing with
    itself depending on where it ran.

    Scope, checked rather than assumed: the production host is Etc/UTC with
    TZ unset in the container, so date.today() has always equalled the UTC
    date there and displayed values were never wrong in production. This is a
    latent defect that surfaces on any non-UTC host — real, but not an
    incident. Verified post-deploy: LITE/MRVL/MSTR hold_days were 3/8/3
    before and after, exactly as a behaviour-preserving fix should leave
    them.

    Naive datetimes are treated as UTC rather than rejected — the column is
    tz-aware, but a naive value can still arrive from a fixture or an older
    row, and assuming local for those would reintroduce the same bug.
    """
    if entry is None:
        return None
    e = entry if entry.tzinfo else entry.replace(tzinfo=timezone.utc)
    today_utc = datetime.now(timezone.utc).date()
    return max((today_utc - e.astimezone(timezone.utc).date()).days, 0)


# ── Portfolio summary ─────────────────────────────────────────────────────────

@router.get("/portfolio")
async def get_portfolio():
    """Live account summary from broker merged with DB P&L stats."""
    broker_data: dict = {}
    try:
        broker = get_broker()
        acct = await asyncio.wait_for(broker.get_account_summary(), timeout=ACCOUNT_SUMMARY_TIMEOUT_SECONDS)
        broker_data = {
            "account_value": float(acct.net_liquidation),
            "cash":          float(acct.cash_balance),
            "buying_power":  float(acct.buying_power),
        }
    except Exception as exc:
        # str(asyncio.TimeoutError()) is "" — an empty message reads as falsy
        # to every `if (broker_error)` check downstream, so a timeout silently
        # presented as "no error" (confirmed live 2026-08-27). Always carry a
        # non-empty description.
        broker_data = {"broker_error": str(exc) or type(exc).__name__}

    try:
        async with AsyncSessionLocal() as session:
            # Only count closes with a KNOWN P&L. Trades auto-closed without a
            # reliable broker exit price are stored with pnl=NULL — including them
            # in the denominator would understate the win rate (a NULL is never a
            # win) and misrepresent total trades.
            stats = (await session.execute(
                select(
                    func.count(Trade.id).label("count"),
                    func.coalesce(func.sum(Trade.pnl), 0).label("total_pnl"),
                    func.coalesce(func.sum(case((Trade.pnl > 0, 1), else_=0)), 0).label("wins"),
                ).where(Trade.status == "closed", Trade.pnl.isnot(None))
            )).one()

            today = date.today()
            trades_today = (await session.execute(
                select(func.count(Trade.id)).where(
                    and_(Trade.status == "closed", func.date(Trade.exit_date) == today)
                )
            )).scalar() or 0

            open_count = (await session.execute(
                select(func.count(Trade.id)).where(Trade.status == "open")
            )).scalar() or 0

        total_trades = int(stats.count or 0)
        total_pnl    = float(stats.total_pnl or 0)
        wins         = int(stats.wins or 0)
        win_rate     = round(wins / total_trades, 3) if total_trades > 0 else 0.0
        # No synthetic substitute: when the broker read fails, return_pct is
        # unknown rather than computed off starting capital + DB P&L.
        acct_value   = broker_data.get("account_value")

        return {"portfolio": {
            **broker_data,
            "total_pnl":        total_pnl,
            "total_trades":     total_trades,
            "win_rate":         win_rate,
            "open_positions":   open_count,
            "trades_today":     trades_today,
            "starting_capital": settings.starting_capital,
            "return_pct":       (
                round((acct_value - settings.starting_capital) / settings.starting_capital * 100, 2)
                if acct_value is not None else None
            ),
        }}
    except Exception as exc:
        return {"portfolio": {**broker_data, "db_error": str(exc)}}


# ── Live positions ─────────────────────────────────────────────────────────────

@router.get("/positions")
async def get_positions():
    """Live broker positions merged with DB trade metadata."""
    try:
        broker = get_broker()
        broker_positions = await broker.get_positions()
    except Exception as exc:
        broker_positions = []
        broker_err = str(exc)
    else:
        broker_err = None

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Trade).where(Trade.status == "open"))
            # Key by (underlying, equity|options) — not bare underlying — so
            # SPY shares and a SPY spread do not clobber each other.
            db_trades = {
                position_identity_key(t.underlying, asset_class_from_trade(t)): t
                for t in result.scalars().all()
            }
    except Exception:
        db_trades = {}

    # Fetch current prices for all symbols via yfinance
    import asyncio, yfinance as yf
    symbols = list({getattr(p, "symbol", "") for p in broker_positions if getattr(p, "symbol", "")})
    price_map: dict[str, float] = {}
    if symbols:
        def _fetch_prices():
            try:
                tickers = yf.Tickers(" ".join(symbols))
                for sym in symbols:
                    try:
                        hist = tickers.tickers[sym].history(period="1d")
                        if not hist.empty:
                            price_map[sym] = float(hist["Close"].iloc[-1])
                    except Exception:
                        pass
            except Exception:
                pass
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _fetch_prices)

    positions = []
    seen = set()

    for pos in broker_positions:
        sym = getattr(pos, "symbol", str(pos))
        underlying = getattr(pos, "underlying", sym)
        broker_asset = (
            "equity" if getattr(pos, "asset_type", "option") == "equity" else "options"
        )
        id_key = position_identity_key(underlying, broker_asset)
        seen.add(id_key)
        # Prefer exact (underlying, asset) match; fall back to underlying-only
        # equity/options pair only when broker did not report asset_type well.
        db = db_trades.get(id_key)
        if db is None and getattr(pos, "asset_type", None) is None:
            db = db_trades.get(position_identity_key(underlying, "equity")) or db_trades.get(
                position_identity_key(underlying, "options")
            )
        qty      = getattr(pos, "quantity", 0)
        avg_cost = float(getattr(pos, "avg_cost", 0) or 0)
        cur_price = price_map.get(sym)
        if cur_price is not None and avg_cost > 0:
            market_value   = round(cur_price * qty, 2)
            unrealized_pnl = round((cur_price - avg_cost) * qty, 2)
        else:
            market_value   = None
            unrealized_pnl = None

        spread_type = db.spread_type if db else None
        # This branch (a broker position matched to a DB trade) previously
        # returned none of asset_type/hold_days/trading_mode/mfe_pnl/mae_pnl —
        # only the separate db_only fallback branch below (open in the DB but
        # not currently at the broker, a rare case) ever included them. Since
        # every live position normally goes through THIS branch, the Trade
        # Desk Positions table — which reads all of those fields — was
        # silently showing wrong defaults for every real position: "OPTIONS"
        # for equities (asset_type undefined), "$0.00" entry credit, blank
        # MFE/MAE, "—" hold days, and "balanced" regardless of the real
        # active trading mode. All of this data already exists on `db` (the
        # matched Trade row) — it just was never included in the response.
        positions.append({
            # Needed by the frontend's manual close action (POST
            # /api/trade-desk/close-position) — no trade to close if there's
            # no matching DB row (an untracked/ghost broker position).
            "id":             str(db.id) if db else None,
            "symbol":         sym,
            "quantity":       qty,
            "avg_cost":       avg_cost,
            "current_price":  cur_price,
            "market_value":   market_value,
            "unrealized_pnl": unrealized_pnl,
            "strategy":       db.strategy if db else "unknown",
            # Tracked: derive from the DB's spread_type (trusted, already
            # correct). Untracked: no DB row means spread_type is always
            # None here, which silently defaulted to "options" for every
            # untracked position regardless of its real asset class — use
            # the broker's own reported asset_type instead (Position.
            # asset_type, set from the live contract's secType).
            "asset_type": (
                ("equity" if (spread_type or "").lower().startswith("equity") else "options")
                if db else
                ("equity" if getattr(pos, "asset_type", "option") == "equity" else "options")
            ),
            "entry_date":     db.entry_date.isoformat() if db and db.entry_date else None,
            "spread_type":    spread_type,
            "hold_days":      _hold_days_utc(db.entry_date) if db else None,
            "trading_mode":   db.trading_mode_at_entry if db else None,
            "approved_by":    db.approved_by if db else None,
            "credit_received": float(db.credit_received) if (db and db.credit_received is not None) else None,
            "mfe_pnl":        float(db.mfe) if (db and db.mfe is not None) else None,
            "mae_pnl":        float(db.mae) if (db and db.mae is not None) else None,
            # A broker position with no matching DB open trade is "untracked" —
            # OlbosTrade did not open it (e.g. pre-existing holdings in a shared
            # paper account). The reconciler flags these as ghost positions; the
            # UI must not present them as OlbosTrade's managed trades or P&L.
            "tracked":        db is not None,
        })

    # Add any DB-open trades not in broker positions (may be paper-only)
    for (_sym, _asset), t in db_trades.items():
        id_key = (_sym, _asset)
        if id_key not in seen:
            positions.append({
                "id":              str(t.id),
                "symbol":          _sym,
                "strategy":        t.strategy,
                "asset_type":      _asset,
                "spread_type":     t.spread_type,
                "entry_date":      t.entry_date.isoformat() if t.entry_date else None,
                "hold_days":       _hold_days_utc(t.entry_date),
                "trading_mode":    t.trading_mode_at_entry,
                "approved_by":     t.approved_by,
                "credit_received": float(t.credit_received or 0),
                "mfe_pnl":         float(t.mfe_pnl or 0),
                "mae_pnl":         float(t.mae_pnl or 0),
                "source":          "db_only",
                "tracked":         True,
            })

    tracked_count = sum(1 for p in positions if p.get("tracked"))
    return {
        "positions": positions,
        "tracked_count": tracked_count,
        "untracked_count": len(positions) - tracked_count,
        **({"broker_error": broker_err} if broker_err else {}),
    }


# ── Trade history ──────────────────────────────────────────────────────────────

@router.get("/history")
async def get_trade_history(
    limit:  int = Query(50, le=500),
    offset: int = Query(0),
    status: Optional[str] = Query(None, description="open | closed | all"),
):
    """Trade history from DB, newest first."""
    try:
        async with AsyncSessionLocal() as session:
            q = select(Trade).order_by(Trade.entry_date.desc()).offset(offset).limit(limit)
            if status and status != "all":
                q = q.where(Trade.status == status)

            trades = (await session.execute(q)).scalars().all()

            count_q = select(func.count(Trade.id))
            if status and status != "all":
                count_q = count_q.where(Trade.status == status)
            total = (await session.execute(count_q)).scalar() or 0

        return {
            "trades": [
                {
                    "id":              str(t.id),
                    "strategy":        t.strategy,
                    "underlying":      t.underlying,
                    "status":          t.status,
                    "entry_date":      t.entry_date.isoformat() if t.entry_date else None,
                    "exit_date":       t.exit_date.isoformat() if t.exit_date else None,
                    "expiration":      t.expiration.isoformat() if t.expiration else None,
                    "short_strike":    float(t.short_strike),
                    "long_strike":     float(t.long_strike),
                    "quantity":        int(getattr(t, "quantity", None) or 0),
                    "credit_received": float(t.credit_received or 0),
                    "cost_to_close":   float(t.cost_to_close or 0),
                    "pnl":             float(t.pnl or 0),
                    "pnl_pct":         float(t.pnl_pct or 0),
                    "mfe":             float(t.mfe) if t.mfe is not None else None,
                    "mae":             float(t.mae) if t.mae is not None else None,
                    "exit_reason":     t.exit_reason,
                    "signal_score":    float(t.signal_score or 0),
                    "hold_days":       max(((t.exit_date or datetime.now(timezone.utc)) - t.entry_date).days, 0) if t.entry_date else None,
                    "trading_mode":    getattr(t, "trading_mode_at_entry", None),
                    "approved_by":     getattr(t, "approved_by", None),
                }
                for t in trades
            ],
            "total":  total,
            "offset": offset,
            "limit":  limit,
        }
    except Exception as exc:
        return {"trades": [], "total": 0, "error": str(exc)}


# ── Strategy toggle ────────────────────────────────────────────────────────────

@router.post("/toggle/{strategy}")
async def toggle_strategy(strategy: str):
    """
    No-op stub retained for backwards compatibility with older clients.
    Per-strategy enable/disable is governed by the trading mode, not by this
    endpoint. Use POST /api/mode/set to change the active mode and strategy set.
    """
    raise HTTPException(
        status_code=410,
        detail={
            "message": "This endpoint is deprecated and has no effect.",
            "action": "Use POST /api/mode/set to change the active trading mode and allowed strategies.",
        },
    )


# ── Greeks summary ────────────────────────────────────────────────────────────

@router.get("/greeks-summary")
async def get_greeks_summary():
    """Portfolio Greeks from the live PortfolioGreeksTracker."""
    try:
        from app.main import _greeks_tracker
        if _greeks_tracker is None:
            return {"net_delta": 0.0, "net_theta": 0.0, "net_vega": 0.0, "net_gamma": 0.0}
        snap = _greeks_tracker.snapshot()
        return {
            "net_delta":     snap.get("net_delta", 0.0),
            "net_theta":     snap.get("net_theta", 0.0),
            "net_vega":      snap.get("net_vega",  0.0),
            "net_gamma":     0.0,
            "needs_hedge":   snap.get("needs_hedge", False),
            "delta_neutral": snap.get("is_delta_neutral", snap.get("delta_neutral", False)),
            "position_count": snap.get("total_position_count", 0),
        }
    except Exception as exc:
        return {"net_delta": 0.0, "net_theta": 0.0, "net_vega": 0.0, "net_gamma": 0.0, "error": str(exc)}
