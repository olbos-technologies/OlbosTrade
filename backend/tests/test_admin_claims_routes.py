"""The operator's way out of a stuck claim, and the audit it cannot skip.

Reconciliation clears claims the broker can speak to. These routes exist for
the ones it cannot — an unsupported lookup, an untransmitted key, a venue that
has forgotten the id. Without them the only remedy is editing the table, which
leaves no record.

The properties worth pinning are about the audit, not the plumbing:

* identity comes from authentication, never from the request, and a caller
  with no identity is refused rather than recorded as nobody;
* a reason is required and must say something;
* a release writes exactly one audit row, and a release that matched nothing
  writes none.
"""
from __future__ import annotations

import os
import uuid
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.routes import admin_claims
from app.models.execution_event import ExecutionEvent
from app.models.position_claim import PositionClaim
from app.services import position_claim

TEST_DB_URL = os.getenv("OLBOS_TEST_DATABASE_URL")

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not TEST_DB_URL,
        reason="needs a real PostgreSQL; set OLBOS_TEST_DATABASE_URL",
    ),
]


@pytest_asyncio.fixture
async def sessions():
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS position_claims"))
        await conn.execute(text("DROP TABLE IF EXISTS execution_events"))
        await conn.run_sync(PositionClaim.__table__.create)
        await conn.run_sync(ExecutionEvent.__table__.create)
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with patch("app.core.database.AsyncSessionLocal", factory):
        yield factory
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS position_claims"))
        await conn.execute(text("DROP TABLE IF EXISTS execution_events"))
    await engine.dispose()


def _sym() -> str:
    return f"T{uuid.uuid4().hex[:6].upper()}"


def _as(identity: dict | None):
    """Pretend the request carries (or does not carry) an authenticated user."""
    return patch.object(admin_claims, "current_user", return_value=identity)


async def _stuck(ticker, reason="venue lost the key"):
    claim = await position_claim.try_claim(ticker, "options")
    await position_claim.mark_submitted(claim)
    await position_claim.mark_unknown(claim, reason)
    return claim


async def _events(factory):
    async with factory() as s:
        return list((await s.execute(
            select(ExecutionEvent).where(ExecutionEvent.kind == "claim_override")
        )).scalars().all())


# ── listing ────────────────────────────────────────────────────────────────

async def test_listing_shows_what_is_blocking_entries(sessions):
    ticker = _sym()
    claim = await _stuck(ticker)

    with _as({"id": "ops-1", "email": "ops@olbostrade"}):
        out = await admin_claims.list_unresolved_claims(object())

    assert out["count"] == 1
    row = out["claims"][0]
    assert row["underlying"] == ticker
    assert row["state"] == position_claim.STATE_UNKNOWN
    assert row["unresolved_reason"] == "venue lost the key"
    assert row["claim_token"] == str(claim.token)
    assert row["releasing_allows_a_new_entry"] is True


async def test_listing_requires_an_identity(sessions):
    """What is blocked is operational detail, not public."""
    from fastapi import HTTPException

    await _stuck(_sym())
    with _as(None), pytest.raises(HTTPException) as err:
        await admin_claims.list_unresolved_claims(object())
    assert err.value.status_code == 403


async def test_a_pending_claim_is_not_listed(sessions):
    """Only claims that need a decision. A pending one is the lease's business."""
    await position_claim.try_claim(_sym(), "options")

    with _as({"id": "ops-1"}):
        out = await admin_claims.list_unresolved_claims(object())

    assert out["count"] == 0


# ── the audited override ───────────────────────────────────────────────────

async def test_releasing_records_the_authenticated_operator_and_reason(sessions):
    ticker = _sym()
    claim = await _stuck(ticker)

    body = admin_claims.ReleaseRequest(reason="venue confirmed no fill by phone")
    with _as({"id": "ops-1", "email": "ops@olbostrade"}):
        out = await admin_claims.release_claim(str(claim.token), body, object())

    assert out["released"] is True
    assert out["operator"] == "ops-1"

    events = await _events(sessions)
    assert len(events) == 1
    assert events[0].payload["operator"] == "ops-1"
    assert events[0].payload["reason"] == "venue confirmed no fill by phone"
    assert events[0].payload["state"] == position_claim.STATE_UNKNOWN
    assert events[0].ticker == ticker

    # The position is usable again.
    assert await position_claim.try_claim(ticker, "options") is not None


async def test_the_operator_cannot_be_supplied_by_the_caller(sessions):
    """Identity is taken from auth, so a body field cannot forge it.

    An operator the caller chooses is not an audit trail. ReleaseRequest
    accepts a reason and nothing else; anything extra is ignored rather than
    trusted.
    """
    claim = await _stuck(_sym())

    body = admin_claims.ReleaseRequest.model_validate(
        {"reason": "cleared", "operator": "someone-else"}
    )
    with _as({"id": "real-operator"}):
        out = await admin_claims.release_claim(str(claim.token), body, object())

    assert out["operator"] == "real-operator"
    assert (await _events(sessions))[0].payload["operator"] == "real-operator"


async def test_an_unauthenticated_release_is_refused_and_audits_nothing(sessions):
    """Better to leave the claim blocked than to record an override by nobody."""
    from fastapi import HTTPException

    ticker = _sym()
    await _stuck(ticker)

    body = admin_claims.ReleaseRequest(reason="trust me")
    with _as(None), pytest.raises(HTTPException) as err:
        await admin_claims.release_claim(str(uuid.uuid4()), body, object())

    assert err.value.status_code == 403
    assert await _events(sessions) == []
    # Still blocking, which is the safe direction.
    assert await position_claim.try_claim(ticker, "options") is None


async def test_a_blank_reason_is_rejected(sessions):
    """The record's whole value is saying why the guard was overridden."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        admin_claims.ReleaseRequest(reason="   ")


async def test_a_missing_reason_is_rejected(sessions):
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        admin_claims.ReleaseRequest()


async def test_releasing_an_unknown_token_is_a_404_and_audits_nothing(sessions):
    from fastapi import HTTPException

    body = admin_claims.ReleaseRequest(reason="cleaning up")
    with _as({"id": "ops-1"}), pytest.raises(HTTPException) as err:
        await admin_claims.release_claim(str(uuid.uuid4()), body, object())

    assert err.value.status_code == 404
    assert await _events(sessions) == []


async def test_a_malformed_token_is_the_same_404(sessions):
    """So the endpoint cannot be used to probe which tokens exist."""
    from fastapi import HTTPException

    body = admin_claims.ReleaseRequest(reason="cleaning up")
    with _as({"id": "ops-1"}), pytest.raises(HTTPException) as err:
        await admin_claims.release_claim("not-a-uuid", body, object())

    assert err.value.status_code == 404


async def test_releasing_twice_only_audits_once(sessions):
    """The second call matched nothing and must not claim it did."""
    from fastapi import HTTPException

    claim = await _stuck(_sym())
    body = admin_claims.ReleaseRequest(reason="confirmed by venue")

    with _as({"id": "ops-1"}):
        await admin_claims.release_claim(str(claim.token), body, object())
        with pytest.raises(HTTPException) as err:
            await admin_claims.release_claim(str(claim.token), body, object())

    assert err.value.status_code == 404
    assert len(await _events(sessions)) == 1


# ── wiring ─────────────────────────────────────────────────────────────────

async def test_the_routes_are_under_the_admin_prefix():
    """The session allowlist matches on PATH alone, so the prefix is the boundary.

    Sharing a prefix with anything public would publish the queue of blocked
    positions and the override that clears them.
    """
    assert admin_claims.router.prefix == "/api/admin/position-claims"


async def test_the_routes_are_registered_on_the_app():
    from app.main import app

    paths = {r.path for r in app.routes}
    assert "/api/admin/position-claims" in paths
    assert "/api/admin/position-claims/{claim_token}/release" in paths
