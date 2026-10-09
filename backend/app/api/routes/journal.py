"""
Journal routes — CRUD wired to journal_entries DB table + analytics via JournalAnalytics.
"""
from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from starlette.requests import HTTPConnection

from app.core.database import AsyncSessionLocal
from app.models.journal_entry import JournalEntry
from app.models.trade import Trade
from app.services.journal_service import JournalAnalytics, JournalEntryOut
from app.services.organization_service import owner_scope

router = APIRouter()
_analytics = JournalAnalytics()


def _owned_by(org_id):
    """Filter restricting a query to one owner's entries.

    `org_id is None` is the single-operator scope and matches the rows
    migration 0039 left unattributed — NOT every row. Writing this as "no
    filter when None" is the shared query these routes used to be.
    """
    if org_id is None:
        return JournalEntry.organization_id.is_(None)
    return JournalEntry.organization_id == org_id


async def _scope(session, conn: HTTPConnection):
    """Resolve the caller's scope, turning a missing identity into a 401."""
    try:
        return await owner_scope(session, conn)
    except PermissionError:
        raise HTTPException(401, "Not authenticated")


def _to_out(e: JournalEntry, trade: Optional[Trade] = None) -> JournalEntryOut:
    return JournalEntryOut(
        id=str(e.id),
        trade_id=str(e.trade_id) if e.trade_id else None,
        strategy=trade.strategy if trade else None,
        underlying=trade.underlying if trade else None,
        pre_trade_thesis=e.pre_trade_thesis or "",
        confidence_level=e.confidence_level or 3,
        market_context=e.market_context or "",
        post_trade_notes=e.post_trade_notes,
        followed_rules=e.followed_rules,
        exit_felt_right=e.exit_felt_right,
        tags=e.tags or [],
        loss_category=e.loss_category,
        mistake_tags=e.mistake_tags or [],
        pnl=float(trade.pnl) if (trade and trade.pnl is not None) else None,
        mfe=float(trade.mfe) if (trade and trade.mfe is not None) else None,
        mae=float(trade.mae) if (trade and trade.mae is not None) else None,
        signal_score=float(trade.signal_score) if (trade and trade.signal_score is not None) else None,
        entry_date=e.created_at.date() if e.created_at else date.today(),
        exit_date=trade.exit_date.date() if (trade and trade.exit_date) else None,
    )


async def _load_all(conn: HTTPConnection) -> list[JournalEntryOut]:
    async with AsyncSessionLocal() as session:
        org_id = await _scope(session, conn)
        entries = (await session.execute(
            select(JournalEntry)
            .where(_owned_by(org_id))
            .order_by(JournalEntry.created_at.desc())
        )).scalars().all()
        trade_ids = [e.trade_id for e in entries if e.trade_id]
        trade_map: dict = {}
        if trade_ids:
            rows = (await session.execute(select(Trade).where(Trade.id.in_(trade_ids)))).scalars().all()
            trade_map = {t.id: t for t in rows}
    return [_to_out(e, trade_map.get(e.trade_id)) for e in entries]


class JournalEntryIn(BaseModel):
    trade_id:         Optional[str] = None
    pre_trade_thesis: str
    confidence_level: int
    market_context:   str

class JournalEntryPatch(BaseModel):
    post_trade_notes: Optional[str]       = None
    followed_rules:   Optional[bool]      = None
    exit_felt_right:  Optional[bool]      = None
    tags:             Optional[list[str]] = None
    loss_category:    Optional[str]       = None
    mistake_tags:     Optional[list[str]] = None


@router.post("/entry", status_code=201)
async def create_entry(entry: JournalEntryIn, conn: HTTPConnection):
    trade_uuid = None
    if entry.trade_id:
        try:
            trade_uuid = uuid.UUID(entry.trade_id)
        except ValueError:
            raise HTTPException(400, "Invalid trade_id UUID")
    async with AsyncSessionLocal() as session:
        org_id = await _scope(session, conn)
        row = JournalEntry(
            id=uuid.uuid4(), trade_id=trade_uuid,
            organization_id=org_id,
            pre_trade_thesis=entry.pre_trade_thesis,
            confidence_level=max(1, min(5, entry.confidence_level)),
            market_context=entry.market_context,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return {"created": True, "id": str(row.id)}


@router.get("/entries")
async def list_entries(conn: HTTPConnection, limit: int = Query(50, le=500), offset: int = Query(0)):
    try:
        async with AsyncSessionLocal() as session:
            org_id = await _scope(session, conn)
            rows = (await session.execute(
                select(JournalEntry).where(_owned_by(org_id))
                .order_by(JournalEntry.created_at.desc()).offset(offset).limit(limit)
            )).scalars().all()
            total = (await session.execute(
                select(func.count(JournalEntry.id)).where(_owned_by(org_id))
            )).scalar() or 0
            trade_ids = [r.trade_id for r in rows if r.trade_id]
            trade_map: dict = {}
            if trade_ids:
                trades = (await session.execute(select(Trade).where(Trade.id.in_(trade_ids)))).scalars().all()
                trade_map = {t.id: t for t in trades}
        return {"entries": [_to_out(r, trade_map.get(r.trade_id)).__dict__ for r in rows], "total": total}
    except HTTPException:
        # An auth or scope failure is not an empty journal. Swallowing it into
        # {"entries": [], "error": ...} renders "you are not allowed to see
        # this" as "you have nothing", which reads as a safe value and is not
        # one.
        raise
    except Exception as exc:
        return {"entries": [], "total": 0, "error": str(exc)}


@router.get("/analytics/tags")
async def get_tag_performance(conn: HTTPConnection):
    entries = await _load_all(conn)
    if not entries:
        return {"tag_performance": [], "message": "No journal entries yet."}
    return {"tag_performance": [p.__dict__ for p in _analytics.tag_performance(entries)]}


@router.get("/analytics/mistakes")
async def get_mistake_frequency(conn: HTTPConnection):
    entries = await _load_all(conn)
    if not entries:
        return {"mistakes": {}, "message": "No journal entries yet."}
    return {"mistakes": _analytics.mistake_frequency(entries)}


@router.get("/analytics/rule-breach-impact")
async def get_rule_breach_impact(conn: HTTPConnection):
    entries = await _load_all(conn)
    if not [e for e in entries if e.followed_rules is not None]:
        return {"impact": None, "message": "No entries with followed_rules data yet."}
    return {"impact": _analytics.rule_breach_impact(entries).__dict__}


@router.get("/review/monthly/{month}")
async def get_monthly_review(month: str, conn: HTTPConnection):
    entries = await _load_all(conn)
    return {"month": month, "review": _analytics.generate_monthly_review(entries, month).__dict__}


@router.get("/{entry_id}")
async def get_entry(entry_id: str, conn: HTTPConnection):
    try:
        uid = uuid.UUID(entry_id)
    except ValueError:
        raise HTTPException(400, "Invalid UUID")
    async with AsyncSessionLocal() as session:
        org_id = await _scope(session, conn)
        # Ownership is part of the lookup, not a check afterwards. Another
        # organization's id must answer exactly as a nonexistent one does —
        # 404, never 403 — or the response confirms the row exists.
        row = (await session.execute(
            select(JournalEntry).where(JournalEntry.id == uid, _owned_by(org_id))
        )).scalar_one_or_none()
        if not row:
            row = (await session.execute(
                select(JournalEntry).where(JournalEntry.trade_id == uid, _owned_by(org_id))
            )).scalar_one_or_none()
        if not row:
            raise HTTPException(404, "Journal entry not found")
        trade = await session.get(Trade, row.trade_id) if row.trade_id else None
    return {"entry": _to_out(row, trade).__dict__}


@router.put("/{entry_id}")
async def update_entry(entry_id: str, patch: JournalEntryPatch, conn: HTTPConnection):
    try:
        uid = uuid.UUID(entry_id)
    except ValueError:
        raise HTTPException(400, "Invalid UUID")
    async with AsyncSessionLocal() as session:
        org_id = await _scope(session, conn)
        row = (await session.execute(
            select(JournalEntry).where(JournalEntry.id == uid, _owned_by(org_id))
        )).scalar_one_or_none()
        if not row:
            raise HTTPException(404, "Journal entry not found")
        if patch.post_trade_notes is not None: row.post_trade_notes = patch.post_trade_notes
        if patch.followed_rules   is not None: row.followed_rules   = patch.followed_rules
        if patch.exit_felt_right  is not None: row.exit_felt_right  = patch.exit_felt_right
        if patch.tags             is not None: row.tags             = patch.tags
        if patch.loss_category    is not None: row.loss_category    = patch.loss_category
        if patch.mistake_tags     is not None: row.mistake_tags     = patch.mistake_tags
        await session.commit()
        await session.refresh(row)
        trade = await session.get(Trade, row.trade_id) if row.trade_id else None
    return {"updated": True, "entry": _to_out(row, trade).__dict__}
