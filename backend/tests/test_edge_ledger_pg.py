"""The ledger's routes and its lock, against real PostgreSQL.

The lock lives in the ORM and the append-only history in a JSONB column, so a mocked session
would never exercise either. Routes are called directly (their auth dependency is pinned
separately), with the session factory patched to a test database.
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.routes import edge_ledger as routes
from app.models.edge_hypothesis import EdgeHypothesis
from app.services import edge_ledger as L

TEST_DB_URL = os.getenv("OLBOS_TEST_DATABASE_URL")
pytestmark = [pytest.mark.asyncio,
              pytest.mark.skipif(not TEST_DB_URL, reason="needs a real PostgreSQL; set OLBOS_TEST_DATABASE_URL")]

CRIT = {"metric": "mean 10-minute move after a drop", "unit": "bp", "baseline": "all days",
        "min_n": 30, "min_effect": 1.0, "cost": 2.0}


def reg(**over):
    p = dict(title="Dip then rebound", statement="After SPY falls from 10:00 to 10:30 ET it rises over 10 minutes.",
             family="Time of Day", rule="Buy the spread at 10:30 ET if 10:00-10:30 was down; close after 10 minutes.",
             universe=["spy", "qqq"], horizon="10 minutes", criteria=dict(CRIT),
             data_plan="The recorder, from 2026-11-17.", retroactive=True)
    p.update(over)
    return routes.RegisterRequest(**p)


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS edge_hypotheses CASCADE"))
        await conn.run_sync(EdgeHypothesis.__table__.create)
    f = async_sessionmaker(bind=engine, expire_on_commit=False)
    with patch("app.core.database.AsyncSessionLocal", f):
        yield f
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS edge_hypotheses CASCADE"))
    await engine.dispose()


async def count(factory):
    async with factory() as s:
        return len((await s.execute(select(EdgeHypothesis))).scalars().all())


# ── registering ──────────────────────────────────────────────────────────────

async def test_registering_locks_a_hypothesis_and_normalises_it(factory):
    out = await routes.register(reg())
    assert out["status"] == "registered" and out["retroactive"] is True and out["results"] == []
    assert out["family"] == "time of day" and out["universe"] == ["SPY", "QQQ"]
    assert out["criteria"]["alpha"] == L.DEFAULT_ALPHA and out["family_size"] == 1
    assert out["required_t"] == pytest.approx(1.645, abs=0.01) and out["locked_at"]


async def test_an_incomplete_registration_is_refused_and_stores_nothing(factory):
    with pytest.raises(HTTPException) as e:
        await routes.register(reg(rule="short", criteria={**CRIT, "min_n": 5}))
    assert e.value.status_code == 422 and any("rule" in p for p in e.value.detail) and any("min_n" in p for p in e.value.detail)
    assert await count(factory) == 0


async def test_every_idea_tried_in_a_family_raises_the_bar(factory):
    first = await routes.register(reg())
    second = await routes.register(reg(title="Lunch lull reversal", family="  TIME of day "))
    other = await routes.register(reg(title="Sell premium", family="premium selling"))
    assert second["family_size"] == 2 and second["required_t"] > first["required_t"]
    assert other["family_size"] == 1
    listed = await routes.list_hypotheses()
    assert listed["total"] == 3 and listed["families"]["time of day"]["tried"] == 2
    assert listed["families"]["time of day"]["required_t"] > listed["families"]["premium selling"]["required_t"]


# ── the lock ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("field,value", [
    ("rule", "A different rule that fits the data better, which is the whole temptation."),
    ("criteria", {**CRIT, "min_effect": 0.01}), ("retroactive", False), ("title", "Renamed"),
    ("family", "another family"), ("universe", ["AAPL"]), ("data_plan", "different data"), ("horizon", "1 day"),
])
async def test_a_registered_hypothesis_cannot_be_edited(factory, field, value):
    out = await routes.register(reg())
    async with factory() as s:
        row = (await s.execute(select(EdgeHypothesis))).scalars().one()
        setattr(row, field, value)
        with pytest.raises(ValueError, match="cannot be edited"):
            await s.commit()
    async with factory() as s:          # and nothing changed on disk
        row = (await s.execute(select(EdgeHypothesis))).scalars().one()
        assert row.as_dict()[field] == out[field]


async def test_the_fields_that_move_are_not_locked(factory):
    await routes.register(reg())
    async with factory() as s:
        row = (await s.execute(select(EdgeHypothesis))).scalars().one()
        row.status = "lead"
        row.verdict_reasons = ["x"]
        await s.commit()


# ── results ──────────────────────────────────────────────────────────────────

def result(**over):
    p = dict(n=36, mean=1.06, t=0.67, sample_start="2026-08-21", sample_end="2026-11-13", notes="SPY 60 days")
    p.update(over)
    return routes.ResultRequest(**p)


async def test_the_dip_study_as_it_came_out_is_recorded_as_not_supported(factory):
    h = await routes.register(reg())
    out = await routes.add_result(h["id"], result())
    assert out["status"] == "not_supported" and "does not cover" in out["verdict_reasons"][0]
    entry = out["results"][0]
    assert entry["verified"] is False and entry["status_after"] == "not_supported" and entry["family_size"] == 1
    assert entry["net_effect"] == pytest.approx(-0.94)


async def test_results_only_ever_append(factory):
    h = await routes.register(reg())
    first = (await routes.add_result(h["id"], result(n=10)))["results"][0]
    out = await routes.add_result(h["id"], result(n=12))
    assert len(out["results"]) == 2 and out["results"][0] == first and out["status"] == "inconclusive"


async def test_a_final_verdict_survives_a_later_better_looking_result(factory):
    h = await routes.register(reg())
    await routes.add_result(h["id"], result())
    out = await routes.add_result(h["id"], result(n=200, mean=9.0, t=6.0))
    assert out["status"] == "not_supported" and len(out["results"]) == 2
    assert "new hypothesis" in out["verdict_reasons"][0]


async def test_typed_in_numbers_that_look_great_are_only_a_lead(factory):
    h = await routes.register(reg(retroactive=False))
    out = await routes.add_result(h["id"], result(n=200, mean=9.0, t=6.0))
    assert out["status"] == "lead" and out["results"][0]["verified"] is False


async def test_a_caller_cannot_claim_their_numbers_are_verified(factory):
    h = await routes.register(reg(retroactive=False))
    sneaky = routes.ResultRequest.model_validate({"n": 200, "mean": 9.0, "t": 6.0, "verified": True,
                                                  "source": "recorder_zero_dte"})
    with pytest.raises(HTTPException) as e:
        await routes.add_result(h["id"], sneaky)
    assert e.value.status_code == 422 and "unknown result source" in e.value.detail[0]
    out = await routes.add_result(h["id"], routes.ResultRequest.model_validate(
        {"n": 200, "mean": 9.0, "t": 6.0, "verified": True}))           # extra field is ignored, not trusted
    assert out["status"] == "lead" and out["results"][0]["verified"] is False


async def test_a_malformed_result_is_refused_and_not_recorded(factory):
    h = await routes.register(reg())
    with pytest.raises(HTTPException) as e:
        await routes.add_result(h["id"], result(n=-3))
    assert e.value.status_code == 422
    assert (await routes.get_hypothesis(h["id"]))["results"] == []


async def test_the_family_size_at_judging_time_is_recorded_with_the_result(factory):
    h = await routes.register(reg())
    await routes.register(reg(title="Another idea in the same family"))
    out = await routes.add_result(h["id"], result(n=5))
    assert out["results"][0]["family_size"] == 2


# ── retiring and lookups ─────────────────────────────────────────────────────

async def test_an_open_idea_can_be_retired_with_a_reason(factory):
    h = await routes.register(reg())
    out = await routes.retire(h["id"], routes.RetireRequest(reason="Superseded by the lunch-lull test."))
    assert out["status"] == "retired" and out["retired_reason"].startswith("Superseded")


async def test_a_verdict_cannot_be_retired_away(factory):
    h = await routes.register(reg())
    await routes.add_result(h["id"], result())
    with pytest.raises(HTTPException) as e:
        await routes.retire(h["id"], routes.RetireRequest(reason="I would rather not have this on the record."))
    assert e.value.status_code == 409
    assert (await routes.get_hypothesis(h["id"]))["status"] == "not_supported"


async def test_retiring_needs_a_real_reason(factory):
    h = await routes.register(reg())
    with pytest.raises(HTTPException) as e:
        await routes.retire(h["id"], routes.RetireRequest(reason="meh"))
    assert e.value.status_code == 422


@pytest.mark.parametrize("bad_id", ["not-a-uuid", "00000000-0000-0000-0000-000000000000"])
async def test_unknown_ids_are_404(factory, bad_id):
    for call in (routes.get_hypothesis(bad_id), routes.add_result(bad_id, result()),
                 routes.retire(bad_id, routes.RetireRequest(reason="x" * 12))):
        with pytest.raises(HTTPException) as e:
            await call
        assert e.value.status_code == 404
