"""Edge Lab hypothesis ledger routes. See services/edge_ledger.py for what the rules are and why.

Nothing here touches orders, signals or risk: it is a notebook with rules. The registration is
locked once written, results can only be appended, and the verdict is computed by the server from
the criteria that were fixed before the test, never supplied by the caller.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import require_api_key
from app.api.rate_limit import rate_limit
from app.services import edge_ledger as ledger

router = APIRouter(dependencies=[Depends(require_api_key), Depends(rate_limit)])


class RegisterRequest(BaseModel):
    title: str
    statement: str
    family: str
    rule: str
    universe: list[str]
    horizon: str
    criteria: dict[str, Any]
    data_plan: str
    retroactive: bool            # no default on purpose: the author must say whether the data was already seen
    registered_by: Optional[str] = Field(default=None, max_length=80)


class ResultRequest(BaseModel):
    source: str = ledger.MANUAL_SOURCE
    n: int
    mean: float
    t: Optional[float] = None
    sample_start: Optional[str] = None
    sample_end: Optional[str] = None
    notes: Optional[str] = Field(default=None, max_length=2000)


class RetireRequest(BaseModel):
    reason: str


async def _family_size(session, family: str) -> int:
    from app.models.edge_hypothesis import EdgeHypothesis
    return int((await session.execute(
        select(func.count()).select_from(EdgeHypothesis).where(EdgeHypothesis.family == family)
    )).scalar_one())


@router.post("")
async def register(req: RegisterRequest):
    """Lock a hypothesis before testing it. Rejected unless the rule, the data plan and the
    pass/fail criteria (minimum sample, minimum effect after costs, cost, baseline) are all stated."""
    from app.core.database import AsyncSessionLocal
    from app.models.edge_hypothesis import EdgeHypothesis

    payload = req.model_dump()
    problems = ledger.validate_registration(payload)
    if problems:
        raise HTTPException(status_code=422, detail=problems)
    row = EdgeHypothesis(
        title=req.title.strip(), statement=req.statement.strip(),
        family=ledger.normalise_family(req.family), rule=req.rule.strip(),
        universe=[s.strip().upper() for s in req.universe], horizon=req.horizon.strip(),
        criteria={**req.criteria, "alpha": req.criteria.get("alpha", ledger.DEFAULT_ALPHA)},
        data_plan=req.data_plan.strip(), retroactive=req.retroactive,
        registered_by=req.registered_by, status=ledger.REGISTERED, results=[],
    )
    async with AsyncSessionLocal() as session:
        session.add(row)
        await session.commit()
        await session.refresh(row)
        size = await _family_size(session, row.family)
    return {**row.as_dict(), "family_size": size, "required_t": round(ledger.required_t(size, row.criteria["alpha"]), 3)}


@router.get("")
async def list_hypotheses():
    from app.core.database import AsyncSessionLocal
    from app.models.edge_hypothesis import EdgeHypothesis

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            select(EdgeHypothesis).order_by(EdgeHypothesis.locked_at.desc()))).scalars().all()
    families: dict[str, int] = {}
    for r in rows:
        families[r.family] = families.get(r.family, 0) + 1
    return {
        "hypotheses": [r.as_dict() for r in rows],
        "families": {f: {"tried": n, "required_t": round(ledger.required_t(n), 3)} for f, n in sorted(families.items())},
        "total": len(rows),
    }


@router.get("/{hypothesis_id}")
async def get_hypothesis(hypothesis_id: str):
    from app.core.database import AsyncSessionLocal
    from app.models.edge_hypothesis import EdgeHypothesis

    async with AsyncSessionLocal() as session:
        row = await _load(session, hypothesis_id, EdgeHypothesis)
        size = await _family_size(session, row.family)
    return {**row.as_dict(), "family_size": size,
            "required_t": round(ledger.required_t(size, (row.criteria or {}).get("alpha", ledger.DEFAULT_ALPHA)), 3)}


@router.post("/{hypothesis_id}/results")
async def add_result(hypothesis_id: str, req: ResultRequest):
    """Append a result and let the server judge it against the criteria fixed at registration."""
    from app.core.database import AsyncSessionLocal
    from app.models.edge_hypothesis import EdgeHypothesis

    result = req.model_dump()
    problems = ledger.validate_result(result)
    if problems:
        raise HTTPException(status_code=422, detail=problems)
    if req.source != ledger.MANUAL_SOURCE and req.source not in ledger.VERIFIED_SOURCES:
        raise HTTPException(status_code=422, detail=[
            f"unknown result source {req.source!r}: only {ledger.MANUAL_SOURCE!r} is accepted today, and a typed-in "
            "result can end an idea but can never support one"])
    # `verified` is set here, from the source, and never read from the request body.
    result["verified"] = req.source in ledger.VERIFIED_SOURCES

    async with AsyncSessionLocal() as session:
        row = await _load(session, hypothesis_id, EdgeHypothesis, lock=True)
        size = await _family_size(session, row.family)
        verdict = ledger.evaluate(row.as_dict(), result, size)
        entry = {**result, "recorded_at": datetime.now(timezone.utc).isoformat(),
                 "status_after": verdict.status, "reasons": verdict.reasons,
                 "net_effect": verdict.net_effect, "required_t": verdict.required_t, "family_size": size}
        row.results = [*(row.results or []), entry]          # append only
        row.status = verdict.status
        row.verdict_reasons = verdict.reasons
        await session.commit()
        await session.refresh(row)
    return row.as_dict()


@router.post("/{hypothesis_id}/retire")
async def retire(hypothesis_id: str, req: RetireRequest):
    """Abandon an idea that is still open. A hypothesis that already has a verdict keeps it."""
    from app.core.database import AsyncSessionLocal
    from app.models.edge_hypothesis import EdgeHypothesis

    if len((req.reason or "").strip()) < 10:
        raise HTTPException(status_code=422, detail=["reason is required (at least 10 characters)"])
    async with AsyncSessionLocal() as session:
        row = await _load(session, hypothesis_id, EdgeHypothesis, lock=True)
        if row.status in ledger.FINAL:
            raise HTTPException(status_code=409, detail=f"already {row.status}: a verdict stays on the record")
        row.status = ledger.RETIRED
        row.retired_reason = req.reason.strip()
        row.verdict_reasons = [f"retired: {req.reason.strip()}"]
        await session.commit()
        await session.refresh(row)
    return row.as_dict()


async def _load(session, hypothesis_id: str, model, lock: bool = False):
    import uuid
    try:
        key = uuid.UUID(hypothesis_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="hypothesis not found")
    q = select(model).where(model.id == key)
    if lock:
        q = q.with_for_update()
    row = (await session.execute(q)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="hypothesis not found")
    return row
