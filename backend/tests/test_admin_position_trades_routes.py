"""The operator's positions-vs-trades view.

It reads across owners, which is what the Positions screen refuses to do, so what is
pinned is who may use it: an identity is required, and only the operator's own scope
may read it. A broker that cannot be read is reported as such, never as "no positions".
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes import admin_claims, admin_position_trades as route

OP = uuid.UUID("aaaaaaaa-0000-4000-8000-00000000000a")
OTHER = uuid.UUID("bbbbbbbb-0000-4000-8000-00000000000b")


def _as(identity):
    return patch.object(admin_claims, "current_user", return_value=identity)


class _Session:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *a):
        return False


def _run(identity, viewer, operator, report=None):
    report = report or {"generated_at": "t", "broker": {"available": True, "error": None}, "items": [], "summary": {}}
    import asyncio
    with _as(identity), \
         patch.object(route, "AsyncSessionLocal", return_value=_Session()), \
         patch.object(route, "scope_or_401", new=AsyncMock(return_value=viewer)), \
         patch.object(route, "designated_operator_id", return_value=operator), \
         patch.object(route, "load_report", new=AsyncMock(return_value=report)) as load:
        out = asyncio.run(route.position_trades(MagicMock()))
    return out, load


def test_a_caller_with_no_identity_is_refused():
    with pytest.raises(HTTPException) as err:
        _run(None, OP, OP)
    assert err.value.status_code == 403


def test_a_signed_in_user_who_is_not_the_operator_is_refused_and_nothing_is_read():
    with pytest.raises(HTTPException) as err:
        import asyncio
        with _as({"id": "u"}), \
             patch.object(route, "AsyncSessionLocal", return_value=_Session()), \
             patch.object(route, "scope_or_401", new=AsyncMock(return_value=OTHER)), \
             patch.object(route, "designated_operator_id", return_value=OP), \
             patch.object(route, "load_report", new=AsyncMock()) as load:
            try:
                asyncio.run(route.position_trades(MagicMock()))
            finally:
                load.assert_not_awaited()
    assert err.value.status_code == 403 and "operator only" in err.value.detail


def test_the_operator_gets_the_report_labelled_with_both_scopes():
    out, load = _run({"id": "op"}, OP, OP)
    assert out["viewer_scope"] == str(OP) and out["operator_scope"] == str(OP)
    assert out["broker"]["available"] is True
    load.assert_awaited_once()


def test_with_no_designated_operator_the_single_operator_scope_may_read_it():
    out, _ = _run({"id": "op"}, None, None)
    assert out["viewer_scope"] == "single-operator" == out["operator_scope"]


def test_an_organization_is_not_the_operator_when_none_is_designated():
    with pytest.raises(HTTPException) as err:
        _run({"id": "u"}, OP, None)
    assert err.value.status_code == 403


def test_it_is_registered_and_needs_a_session():
    from app.api.auth_deps import is_public_path
    from app.main import app

    assert route.router.prefix == "/api/admin/position-trades"
    assert is_public_path("/api/health") is True, "the check is not reading the real allowlist"
    assert is_public_path("/api/admin/position-trades") is False
    assert "/api/admin/position-trades" in {r.path for r in app.routes}


# ── an unreadable broker is not "no positions" ─────────────────────────────

@pytest.mark.asyncio
async def test_a_broker_that_cannot_be_read_is_reported_not_shown_as_empty():
    from app.services import position_trade_report as rep

    broker = MagicMock()
    broker.get_positions = AsyncMock(side_effect=ConnectionError("gateway down"))
    with patch("app.broker.broker_factory.get_broker", return_value=broker):
        out = await rep.load_report(OP, OP)
    assert out["broker"]["available"] is False and "gateway down" in out["broker"]["error"]
    assert out["items"] == [] and out["summary"] is None


@pytest.mark.asyncio
async def test_zero_quantity_broker_rows_are_not_holdings():
    from app.services import position_trade_report as rep

    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[SimpleNamespace(underlying="AAPL", quantity=0, asset_type="equity")])
    session = MagicMock()
    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=_Session2(session)):
        out = await rep.load_report(OP, OP)
    assert out["items"] == []


class _Session2:
    """An async session whose queries all return nothing."""
    def __init__(self, _):
        pass

    async def __aenter__(self):
        s = MagicMock()
        res = MagicMock()
        res.scalars.return_value.all.return_value = []
        s.execute = AsyncMock(return_value=res)
        return s

    async def __aexit__(self, *a):
        return False
