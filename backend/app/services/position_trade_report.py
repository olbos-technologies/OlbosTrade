"""The data behind the positions-vs-trades page: broker holdings next to every trade
row recorded for them, whoever owns it, with the reason they do or do not line up.

This reads ACROSS owners, which is exactly what the Positions screen refuses to do,
so it is for the operator only (the route enforces that). It is a read: it places,
cancels and changes nothing, and the ownership comparison is made in Python on the
rows it fetched, never by a hand-rolled filter on the ownership column.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select

from app.services.position_trade_diagnosis import (
    BrokerPosition, Diagnosis, ROW_NO_BROKER, TRACKED, TradeRow, diagnose,
    owner_label, visible_to_positions_page,
)
from app.services.trade_identity import asset_class_from_trade
from app.services.position_reconciler import _trade_direction

#: Rows shown per holding, newest first. The point is the recent history.
MAX_ROWS_PER_HOLDING = 8
MAX_ORPHAN_GROUPS = 50


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if isinstance(dt, datetime) else None


def to_row(trade: Any) -> TradeRow:
    return TradeRow(
        id=str(getattr(trade, "id", "")),
        status=str(getattr(trade, "status", "") or "").lower(),
        strategy=str(getattr(trade, "strategy", "") or ""),
        asset_class=asset_class_from_trade(trade),
        direction=_trade_direction(trade),
        quantity=getattr(trade, "quantity", None),
        organization_id=getattr(trade, "organization_id", None),
        entry_date=getattr(trade, "entry_date", None),
        exit_date=getattr(trade, "exit_date", None),
        approved_by=getattr(trade, "approved_by", None),
    )


def _row_json(r: TradeRow, viewer, operator) -> dict:
    return {
        "id": r.id, "status": r.status, "strategy": r.strategy,
        "asset_class": r.asset_class,
        "direction": {1: "long", -1: "short"}.get(r.direction),
        "quantity": r.quantity,
        "owner": owner_label(r.organization_id, viewer, operator),
        "shown_on_positions_page": visible_to_positions_page(r, viewer),
        "entry_date": _iso(r.entry_date), "exit_date": _iso(r.exit_date),
        "approved_by": r.approved_by,
    }


def _positions_page_label(rows: list[TradeRow], viewer) -> str:
    """What the Positions screen prints in the Strategy column for this holding."""
    seen = [r for r in rows if r.status == "open" and visible_to_positions_page(r, viewer)]
    return seen[0].strategy if seen else "UNKNOWN"


def build_items(
    broker_positions: list[BrokerPosition],
    rows_by_symbol: dict[str, list[TradeRow]],
    claims_by_symbol: dict[str, list[dict]],
    viewer: Optional[uuid.UUID],
    operator: Optional[uuid.UUID],
) -> list[dict]:
    """Pure assembly of the report items, so it can be tested without a database."""
    items: list[dict] = []
    held = set()
    for pos in broker_positions:
        key = (pos.symbol, pos.asset_class)
        held.add(key)
        rows = rows_by_symbol.get(pos.symbol, [])
        same = [r for r in rows if r.asset_class == pos.asset_class]
        d: Diagnosis = diagnose(pos, rows, viewer, operator)
        items.append({
            "symbol": pos.symbol, "asset_class": pos.asset_class,
            "broker_quantity": pos.quantity,
            "code": d.code, "severity": d.severity, "explanation": d.explanation,
            "positions_page_shows": _positions_page_label(same, viewer),
            "trades": [_row_json(r, viewer, operator) for r in same[:MAX_ROWS_PER_HOLDING]],
            "claims": claims_by_symbol.get(pos.symbol, []),
        })
    # open rows with nothing at the broker
    orphans = 0
    for sym, rows in sorted(rows_by_symbol.items()):
        for cls in sorted({r.asset_class for r in rows}):
            if (sym, cls) in held:
                continue
            open_rows = [r for r in rows if r.asset_class == cls and r.status == "open"]
            if not open_rows:
                continue
            orphans += 1
            if orphans > MAX_ORPHAN_GROUPS:
                break
            d = diagnose(None, [r for r in rows if r.asset_class == cls], viewer, operator)
            items.append({
                "symbol": sym, "asset_class": cls, "broker_quantity": None,
                "code": d.code, "severity": d.severity, "explanation": d.explanation,
                "positions_page_shows": _positions_page_label(open_rows, viewer),
                "trades": [_row_json(r, viewer, operator) for r in open_rows[:MAX_ROWS_PER_HOLDING]],
                "claims": claims_by_symbol.get(sym, []),
            })
    order = {"attention": 0, "info": 1, "ok": 2}
    items.sort(key=lambda i: (order[i["severity"]], i["symbol"]))
    return items


def summarise(items: list[dict]) -> dict:
    return {
        "holdings": sum(1 for i in items if i["broker_quantity"] is not None),
        "tracked": sum(1 for i in items if i["code"] == TRACKED),
        "attention": sum(1 for i in items if i["severity"] == "attention"),
        "rows_without_position": sum(1 for i in items if i["code"] == ROW_NO_BROKER),
    }


async def load_report(viewer: Optional[uuid.UUID], operator: Optional[uuid.UUID]) -> dict:
    """Broker positions + every trade row for those names (any owner) + their claims."""
    from app.broker.broker_factory import get_broker
    from app.core.database import AsyncSessionLocal
    from app.models.trade import Trade
    from app.services.trade_scope import designated_operator_id  # noqa: F401  (route resolves it)

    broker_error: Optional[str] = None
    positions: list[BrokerPosition] = []
    try:
        for p in await get_broker().get_positions():
            qty = int(getattr(p, "quantity", 0) or 0)
            if not qty:
                continue
            cls = "equity" if getattr(p, "asset_type", "option") == "equity" else "options"
            positions.append(BrokerPosition(str(p.underlying).upper(), cls, qty))
    except Exception as exc:   # an unreadable broker is not "no positions"
        broker_error = f"{type(exc).__name__}: {str(exc)[:160]}"

    base = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "broker": {"available": broker_error is None, "error": broker_error},
    }
    if broker_error is not None:
        return {**base, "items": [], "summary": None}

    symbols = sorted({p.symbol for p in positions})
    async with AsyncSessionLocal() as session:
        # No ownership filter on purpose: the point is to see rows the Positions
        # screen hides. Only the underlying (and open status for orphans) narrows it.
        by_symbol = (await session.execute(
            select(Trade).where(Trade.underlying.in_(symbols))
            .order_by(Trade.entry_date.desc()).limit(500)
        )).scalars().all() if symbols else []
        open_rows = (await session.execute(
            select(Trade).where(Trade.status == "open").order_by(Trade.entry_date.desc()).limit(500)
        )).scalars().all()

    rows_by_symbol: dict[str, list[TradeRow]] = {}
    seen_ids: set[str] = set()
    for t in [*by_symbol, *open_rows]:
        r = to_row(t)
        if r.id in seen_ids:
            continue
        seen_ids.add(r.id)
        rows_by_symbol.setdefault(str(t.underlying).upper(), []).append(r)
    from app.api.routes.admin_claims import unresolved_claims_by_symbol
    claims_by_symbol = await unresolved_claims_by_symbol()

    items = build_items(positions, rows_by_symbol, claims_by_symbol, viewer, operator)
    return {**base, "items": items, "summary": summarise(items)}
