"""Portfolio routes — heat, exposure and concentration over open positions."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.requests import HTTPConnection

from app.api.deps import require_api_key
from app.api.scope_deps import scope_or_401
from app.services.trade_scope import owned_by

from app.services.account_state import get_account_value

router = APIRouter()


@router.get("/heat")
async def portfolio_heat(conn: HTTPConnection):
    """Portfolio heat (% of capital at risk), exposures, and concentration flags.

    This is the *display* of the caller's own heat. It is not the gate: the
    guardrail that refuses an entry reads `_portfolio_heat_fraction` in
    `trade_desk.py`, which stays desk-wide on purpose — see the note there.
    So this number can be lower than the one that blocks an entry, and that
    is the intended relationship, not a bug.
    """
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal
    from app.models.trade import Trade
    from app.services.portfolio_engine import (
        compute_portfolio_risk, position_risk_basis, position_risk_dollars, sector_for,
    )

    account_value = await get_account_value()

    positions: list[dict] = []
    try:
        async with AsyncSessionLocal() as session:
            org_id = await scope_or_401(session, conn)
            open_trades = (await session.execute(
                select(Trade).where(owned_by(org_id), Trade.status == "open")
            )).scalars().all()
        for t in open_trades:
            positions.append({
                "underlying": t.underlying,
                "risk_dollars": position_risk_dollars(t),
                "risk_basis": position_risk_basis(t),
                "sector": sector_for(t.underlying),
            })
    except HTTPException:
        # The scope resolution above raises HTTPException(401) when auth is
        # on and the request carries no identity. HTTPException IS an
        # Exception, so without this the handler below would swallow the
        # refusal and answer with an empty, successful-looking payload —
        # a failed read wearing the costume of a safe value.
        raise
    except Exception as exc:
        return {"error": str(exc),
                **compute_portfolio_risk([], account_value)}

    return compute_portfolio_risk(positions, account_value)


@router.get("/open-orders")
async def portfolio_open_orders(conn: HTTPConnection):
    """Resting broker orders, and which open positions have no stop.

    Read-only: it reads the order book, it never places, modifies or cancels
    anything.

    The list itself is secondary. The number that matters is
    `positions_without_stop` — equity entries go in as brackets with a GTC
    stop child (see IBKRClient.place_equity_order), but positions the
    reconciler adopted from the broker never had a bracket, and `trades` has
    no stop column, so until now nothing could tell a protected position from
    an unprotected one.

    `source` is load-bearing. An empty order list means "no resting orders"
    only when source == "refreshed"; on a cache fall-back it may just mean the
    cache was never populated, and `unprotected_is_reliable` says so rather
    than letting an empty list read as a clean bill of health.
    """
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal
    from app.models.trade import Trade
    from app.broker.broker_factory import get_broker
    from app.broker.ibkr_coordinator import Priority, ibkr_coordinator

    # Establish the caller BEFORE reading anything. The order book is
    # broker-wide — one account, every tenant's resting orders — and the two
    # early returns below would otherwise hand back the broker's own shape
    # without ever asking who is calling. The route sits behind the auth
    # allowlist too; this is the layer that does not depend on a path list
    # staying correct.
    async with AsyncSessionLocal() as _scope_db:
        org_id = await scope_or_401(_scope_db, conn)

    broker = get_broker()
    if not hasattr(broker, "get_open_orders"):
        return {"available": False,
                "reason": f"{type(broker).__name__} does not expose an order book"}

    try:
        result = await ibkr_coordinator.submit(
            Priority.P1, broker.get_open_orders,
            key="open_orders", req_type="OPEN_ORDERS", timeout=15.0,
        )
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}

    orders = result.get("orders", [])
    source = result.get("source", "unknown")

    protected = {
        (o.get("symbol") or "").upper()
        for o in orders
        if o.get("is_protective") and (o.get("remaining") or 0) > 0
    }

    try:
        async with AsyncSessionLocal() as session:
            open_trades = (await session.execute(
                select(Trade).where(owned_by(org_id), Trade.status == "open")
            )).scalars().all()
    except HTTPException:
        # The scope resolution above raises HTTPException(401) when auth is
        # on and the request carries no identity. HTTPException IS an
        # Exception, so without this the handler below would swallow the
        # refusal and answer with an empty, successful-looking payload —
        # a failed read wearing the costume of a safe value.
        raise
    except Exception as exc:
        return {"available": True, "source": source, "orders": orders,
                "positions_error": f"{type(exc).__name__}: {exc}"}

    unprotected = []
    for t in open_trades:
        sym = (t.underlying or "").upper()
        if sym and sym not in protected:
            unprotected.append({
                "ticker": sym,
                "quantity": t.quantity,
                "spread_type": t.spread_type,
                # Adopted positions never had a bracket submitted for them —
                # worth surfacing, because it explains the gap rather than
                # leaving it looking like a lost order.
                "adopted_from_broker": (t.strategy or "") == "adopted_untracked",
            })

    return {
        "available": True,
        "source": source,
        "unprotected_is_reliable": source == "refreshed",
        "order_count": len(orders),
        "protective_order_count": sum(1 for o in orders if o.get("is_protective")),
        "protected_tickers": sorted(protected),
        "open_position_count": len(open_trades),
        "positions_without_stop": unprotected,
        "orders": orders,
    }


class CancelOrdersRequest(BaseModel):
    order_ids: list[int]


class BackfillStopsRequest(BaseModel):
    # Defaults to a rehearsal. Writing a stop lowers what the risk gates
    # measure, so the write is the deliberate step, never the accidental one.
    dry_run: bool = True


@router.post("/backfill-stops", dependencies=[Depends(require_api_key)])
async def backfill_stops(body: BackfillStopsRequest):
    """Record missing equity stops from the broker's live protective orders.

    Positions adopted by the reconciler carry no stop — that path has no entry
    plan to copy one from, so it writes placeholders. The broker usually holds
    a real protective stop for them anyway; it just never reached the row.
    Until it does, portfolio heat reports full notional for those positions.

    Submits no orders. Writes one column, on open equity rows that have no
    stop this system already believes. See stop_backfill.py for the coverage,
    direction and freshness rules — every one of them fails closed to leaving
    the row alone.

    Lowering measured risk loosens the heat and concentration gates, so this
    defaults to dry_run and reports exactly what it would change.
    """
    from app.broker.broker_factory import get_broker
    from app.services.stop_backfill import backfill_equity_stops

    result = await backfill_equity_stops(get_broker(), dry_run=body.dry_run)
    if result.get("status") == "error":
        raise HTTPException(502, result.get("reason") or "stop backfill failed")
    if result.get("status") == "unavailable":
        raise HTTPException(503, result.get("reason") or "order book unverified")
    return result


@router.post("/cancel-orders", dependencies=[Depends(require_api_key)])
async def cancel_orders(body: CancelOrdersRequest):
    """Cancel specific resting orders by IBKR order id.

    Exists because "cancel the orphans" had no mechanism inside the app, and
    doing it in TWS on 2026-08-29 silently changed nothing — all 20 were still
    live on the next refreshed read, with no visible error to diagnose. This
    route reports IBKR's per-order answer, runs against the exact account the
    gateway is connected to, and takes the same ids reqAllOpenOrders reports,
    which removes three of the four theories at once.

    **It refuses to cancel any order protecting a live position.** Cancelling
    a resting stop is the one action here that can increase risk rather than
    reduce it, and a fat-fingered id must not be able to strip MRVL's bracket.
    Protected ids are rejected as a group, before anything is sent — a partial
    cancel that removed one leg of a live bracket would be worse than doing
    nothing.
    """
    from app.broker.broker_factory import get_broker

    if not body.order_ids:
        raise HTTPException(400, "order_ids is empty")

    broker = get_broker()
    if not hasattr(broker, "cancel_orders_by_id"):
        raise HTTPException(
            501, f"{type(broker).__name__} cannot cancel by order id")

    # Read the book first: we need to know which ids guard a real position.
    try:
        book = await broker.get_open_orders(refresh=True)
    except Exception as exc:
        raise HTTPException(502, f"could not read the order book: {exc}")
    if book.get("source") != "refreshed":
        # A cache fall-back cannot prove an order is unprotected.
        raise HTTPException(
            503, "order book came from cache — refusing to cancel on unverified state")

    held: set[str] = set()
    try:
        for p in await broker.get_positions():
            sym = (getattr(p, "symbol", "") or "").upper()
            if sym:
                held.add(sym)
    except Exception as exc:
        raise HTTPException(502, f"could not read positions: {exc}")

    orders = {int(o["order_id"]): o for o in book.get("orders", []) if o.get("order_id")}
    protected = [
        oid for oid in body.order_ids
        if oid in orders and (orders[oid].get("symbol") or "").upper() in held
    ]
    if protected:
        raise HTTPException(409, {
            "error": "refused — these orders belong to symbols with a live position",
            "protected_order_ids": protected,
            "note": ("Cancelling a resting stop on a held position removes its "
                     "protection. Nothing was sent."),
        })

    results = await broker.cancel_orders_by_id(body.order_ids)

    # Verify against a fresh read rather than trusting the send.
    try:
        after = await broker.get_open_orders(refresh=True)
        still_open = {int(o["order_id"]) for o in after.get("orders", []) if o.get("order_id")}
    except Exception:
        still_open = None

    for r in results:
        if still_open is None:
            r["verified"] = "unknown — could not re-read the order book"
        elif r["order_id"] in still_open:
            r["verified"] = "STILL OPEN — IBKR did not cancel it"
        else:
            r["verified"] = "gone from the order book"

    gone = sum(1 for r in results if r.get("verified") == "gone from the order book")
    return {
        "requested": len(body.order_ids),
        "confirmed_cancelled": gone,
        "still_open": len(results) - gone,
        "results": results,
        "order_count_after": after.get("order_count") if still_open is not None else None,
    }


@router.get("/allocation")
async def portfolio_allocation(method: str = "blended"):
    """
    Target capital weights per strategy from the Dynamic Capital Allocation
    Engine, driven by each strategy's health (score/vol/expectancy) and the
    meta-strategy tilt (regime × health). The remainder is held as cash.
    """
    from app.api.routes.strategy import _evaluate_health
    from app.services.allocation_engine import (
        StrategyAlloc, AllocationConstraints, allocate, METHODS,
    )
    from app.services.meta_strategy import decide, tilts

    if method not in METHODS:
        return {"error": f"unknown method '{method}'", "methods": list(METHODS)}

    try:
        health = await _evaluate_health(min_sample=20)
    except Exception as exc:
        return {"error": str(exc), "weights": {}, "cash_weight": 1.0}

    from app.main import _current_regime
    regime = getattr(getattr(_current_regime, "regime", None), "value", None) or "unknown"
    tilt_map = tilts(decide(regime, [h.as_dict() for h in health]))

    inputs = [
        StrategyAlloc(
            strategy=h.strategy, score=h.score, volatility=h.volatility,
            expectancy=h.expectancy,
            tilt=tilt_map.get(h.strategy, 0.0),
        )
        for h in health
    ]
    result = allocate(inputs, method=method, constraints=AllocationConstraints())
    return {"regime": regime, **result.as_dict()}


@router.get("/correlation")
async def portfolio_correlation(conn: HTTPConnection):
    """
    Correlation clusters across open positions' underlyings — flags when
    distinct tickers move together enough to behave as one concentrated
    position (something the underlying/sector concentration flags on
    /heat can't see).
    """
    import asyncio

    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal
    from app.main import _yf_bars
    from app.models.trade import Trade
    from app.services.portfolio_engine import (
        CORRELATION_THRESHOLD,
        align_price_series,
        cluster_concentration_flags,
        compute_correlation_clusters,
        position_risk_dollars,
    )

    account_value = await get_account_value()
    empty = {
        "status": "insufficient_data",
        "tickers": [],
        "correlation_matrix": None,
        "clusters": [],
        "concentration_flags": [],
        "excluded_symbols": [],
        "lookback_days": 60,
        "threshold": CORRELATION_THRESHOLD,
    }

    try:
        async with AsyncSessionLocal() as session:
            org_id = await scope_or_401(session, conn)
            open_trades = (await session.execute(
                select(Trade).where(owned_by(org_id), Trade.status == "open")
            )).scalars().all()
    except HTTPException:
        # The scope resolution above raises HTTPException(401) when auth is
        # on and the request carries no identity. HTTPException IS an
        # Exception, so without this the handler below would swallow the
        # refusal and answer with an empty, successful-looking payload —
        # a failed read wearing the costume of a safe value.
        raise
    except Exception as exc:
        return {**empty, "status": "error", "error": str(exc)}

    risk_by_underlying: dict[str, float] = {}
    for t in open_trades:
        risk_by_underlying[t.underlying] = risk_by_underlying.get(t.underlying, 0.0) + position_risk_dollars(t)

    tickers = sorted(risk_by_underlying.keys())
    if len(tickers) < 2:
        return {**empty, "reason": f"only {len(tickers)} distinct open underlying(s), need >= 2"}

    sem = asyncio.Semaphore(5)
    excluded_symbols: list[dict] = []

    async def _fetch(ticker: str):
        async with sem:
            try:
                bars = await _yf_bars(ticker, limit=60)
                return ticker, bars
            except Exception as exc:
                excluded_symbols.append({"ticker": ticker, "reason": f"fetch failed: {exc}"})
                return ticker, []

    fetched = await asyncio.gather(*(_fetch(t) for t in tickers))
    bars_by_ticker = {t: bars for t, bars in fetched if bars}

    aligned, align_excluded = align_price_series(bars_by_ticker)
    excluded_symbols.extend(align_excluded)

    if len(aligned) < 2:
        return {
            **empty,
            "reason": "insufficient overlapping price history across positions",
            "excluded_symbols": excluded_symbols,
        }

    clustered = compute_correlation_clusters(aligned)
    clusters, flags = cluster_concentration_flags(clustered["clusters"], risk_by_underlying, account_value)

    return {
        "status": "ok",
        "tickers": clustered["tickers"],
        "correlation_matrix": clustered["correlation_matrix"],
        "clusters": clusters,
        "concentration_flags": flags,
        "excluded_symbols": excluded_symbols,
        "lookback_days": 60,
        "threshold": CORRELATION_THRESHOLD,
    }


@router.get("/rotation-performance")
async def portfolio_rotation_performance(conn: HTTPConnection):
    """
    Rotation-performance ledger — aggregate stats on the closed side of
    every position_rotation.py rotation close. Reports honestly on
    whether closing that position was a good call in hindsight; does not
    attempt to compare against whatever new position it enabled (no
    structural link exists between a rotation close and the specific
    entry it freed a slot for).
    """
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal
    from app.models.trade import Trade
    from app.services.rotation_ledger import compute_rotation_ledger_stats

    empty = {
        "status": "no_rotations_yet",
        "total": 0, "total_pnl": 0.0, "avg_pnl": None,
        "win_rate": None, "avg_hold_days": None,
        "by_regime": {}, "recent": [],
    }

    try:
        async with AsyncSessionLocal() as session:
            org_id = await scope_or_401(session, conn)
            rows = (await session.execute(
                select(Trade).where(owned_by(org_id),
                                    Trade.exit_reason == "position_rotation")
            )).scalars().all()
    except HTTPException:
        # The scope resolution above raises HTTPException(401) when auth is
        # on and the request carries no identity. HTTPException IS an
        # Exception, so without this the handler below would swallow the
        # refusal and answer with an empty, successful-looking payload —
        # a failed read wearing the costume of a safe value.
        raise
    except Exception as exc:
        return {**empty, "status": "error", "error": str(exc)}

    rotations: list[dict] = []
    for t in rows:
        # Skip closes with unknown P&L — counting them as $0 would pollute
        # win rate and total_pnl with a fabricated flat trade, matching
        # analytics.py's own convention for the same situation.
        if t.pnl is None:
            continue
        entry = t.entry_date.date() if t.entry_date else None
        exit_d = t.exit_date.date() if t.exit_date else None
        rotations.append({
            "trade_id": str(t.id),
            "ticker": t.underlying,
            "pnl": float(t.pnl),
            "regime": t.regime,
            "entry_date": str(entry) if entry else None,
            "exit_date": str(exit_d) if exit_d else None,
            "hold_days": (exit_d - entry).days if entry and exit_d else None,
            "exit_reason": t.exit_reason,
        })

    if not rotations:
        return empty

    return {"status": "ok", **compute_rotation_ledger_stats(rotations)}


@router.get("/rotation-activity")
async def portfolio_rotation_activity(limit: int = 20):
    """
    Rotation activity feed — a chronological read of what
    position_rotation.py actually closed and why (the ranking signals
    captured on every rotation receipt: quality_score, in_flagged_cluster,
    confidence, unrealized_pnl_at_decision). Complementary to
    /rotation-performance: that route reports financial outcome after the
    fact from Trade rows; this one reports the decision itself from
    ExecutionEvent, forward-looking rather than backward-looking. Does not
    attempt to show what new position a freed slot went to — no
    structural link exists between a rotation close and the entry it
    enabled (same known gap /rotation-performance already documents).
    """
    from sqlalchemy import select
    from app.core.database import AsyncSessionLocal
    from app.models.execution_event import ExecutionEvent

    empty = {"status": "no_rotations_yet", "events": []}

    try:
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                select(ExecutionEvent)
                .where(
                    ExecutionEvent.kind == "execution",
                    ExecutionEvent.payload["closed_by"].astext == "position_rotation",
                )
                .order_by(ExecutionEvent.created_at.desc())
                .limit(limit)
            )).scalars().all()
    except Exception as exc:
        return {**empty, "status": "error", "error": str(exc)}

    if not rows:
        return empty

    events = []
    for r in rows:
        p = r.payload or {}
        events.append({
            "id": str(r.id),
            "ticker": r.ticker,
            # close_equity_trade()'s receipt has no asset_type key at all
            # (only close_options_trade()'s does) — default to equity.
            "asset_type": p.get("asset_type") or "equity",
            "status": p.get("status"),
            "quality_score": p.get("quality_score"),
            "in_flagged_cluster": p.get("in_flagged_cluster"),
            "confidence": p.get("confidence"),
            "unrealized_pnl_at_decision": p.get("unrealized_pnl_at_decision"),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    return {"status": "ok", "events": events}
