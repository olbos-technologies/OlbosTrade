"""The operator's drill for the entry gate, and the record it leaves.

Before enforcing the gate an operator has to watch it meet a known mismatch, a
broker that has gone away, and the recovery - and none of that can be staged by
waiting for the right entry. The drill route runs the same evaluation on demand.

What is pinned here is what makes the drill trustworthy:

  * it needs an identity, like the claims routes;
  * it can place nothing and write nothing, so drilling does not pollute the
    record observe is building;
  * it reports each outcome faithfully - clean, unresolved, unavailable and its
    cause - and says what each mode would do with it;
  * the record route reflects what the gate wrote.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes import admin_claims, admin_entry_gate as route
from app.core.config import settings
from app.services import reconciliation_entry_gate as gate


def _as(identity):
    return patch.object(admin_claims, "current_user", return_value=identity)


def _assess(**kw):
    return AsyncMock(return_value=gate.GateAssessment(**kw))


@pytest.fixture(autouse=True)
def _broker(monkeypatch):
    monkeypatch.setattr(settings, "broker", "ibkr")
    with patch("app.broker.broker_factory.get_broker", return_value=MagicMock()):
        yield


# ── identity ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("handler", ["evaluate_now", "records"])
async def test_both_routes_refuse_a_caller_with_no_identity(handler):
    with _as(None), pytest.raises(HTTPException) as err:
        await getattr(route, handler)(object())
    assert err.value.status_code == 403


# ── the drill ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_clean_book_is_reported_clean_and_an_entry_would_proceed(monkeypatch):
    monkeypatch.setattr(settings, "reconciliation_entry_gate_mode", "observe")
    with _as({"id": "ops"}), patch.object(gate, "evaluate_entry_reconciliation", new=_assess(available=True)):
        out = await route.evaluate_now(object())
    assert out["clean"] is True and out["available"] is True and out["reasons"] == []
    assert out["an_entry_now_would"] == {"observe": "proceed", "enforce": "proceed"}
    assert (out["mode"], out["scope"], out["broker"]) == ("observe", "single-operator", "ibkr")
    assert out["applies_to_current_broker"] is True


@pytest.mark.asyncio
async def test_the_drill_reports_the_scope_the_check_was_made_in():
    verdict = _assess(available=True, scope="aaaaaaaa-0000-4000-8000-0000000000c1")
    with _as({"id": "ops"}), patch.object(gate, "evaluate_entry_reconciliation", new=verdict):
        out = await route.evaluate_now(object())
    assert out["scope"] == "aaaaaaaa-0000-4000-8000-0000000000c1"


@pytest.mark.asyncio
async def test_a_known_mismatch_is_reported_with_what_each_mode_would_do():
    verdict = _assess(available=True, findings=(("direction_mismatch", "AAPL"),),
                      explained={"NVDA": "untracked_at_broker: pending_trade"})
    with _as({"id": "ops"}), patch.object(gate, "evaluate_entry_reconciliation", new=verdict):
        out = await route.evaluate_now(object())
    assert out["clean"] is False and out["reasons"] == ["direction_mismatch:AAPL"]
    assert out["explained"] == {"NVDA": "untracked_at_broker: pending_trade"}
    assert out["an_entry_now_would"]["observe"] == "proceed, and record would_veto"
    assert out["an_entry_now_would"]["enforce"] == "be vetoed: reconciliation_unresolved"


@pytest.mark.asyncio
async def test_a_disconnected_broker_is_reported_as_unavailable_with_its_cause():
    verdict = _assess(available=False, unavailable_cause="broker_unavailable")
    with _as({"id": "ops"}), patch.object(gate, "evaluate_entry_reconciliation", new=verdict):
        out = await route.evaluate_now(object())
    assert out["clean"] is False and out["available"] is False
    assert out["unavailable_cause"] == "broker_unavailable"
    assert out["an_entry_now_would"]["observe"] == "proceed, and record unavailable"
    assert out["an_entry_now_would"]["enforce"] == "be vetoed: reconciliation_unavailable"


@pytest.mark.asyncio
async def test_the_drill_follows_a_real_disconnect_and_recovery():
    """Through the real reconciler: down, then back. Neither verdict sticks."""
    from app.services.position_reconciler import ReconciliationResult  # noqa: F401
    from test_reconciliation_entry_gate import book
    from test_signed_reconciliation import eq_pos, row

    with _as({"id": "ops"}):
        with book(broker_error=ConnectionError("gateway down")) as broker:
            with patch("app.broker.broker_factory.get_broker", return_value=broker):
                down = await route.evaluate_now(object())
        with book(positions=[eq_pos("AAPL", 10)], open_trades=[row("AAPL", 10)]) as broker:
            with patch("app.broker.broker_factory.get_broker", return_value=broker):
                up = await route.evaluate_now(object())
    assert down["available"] is False and down["unavailable_cause"] == "broker_unavailable"
    assert up["clean"] is True


@pytest.mark.asyncio
async def test_it_reports_when_the_current_broker_is_not_one_the_gate_covers(monkeypatch):
    monkeypatch.setattr(settings, "broker", "alpaca")
    with _as({"id": "ops"}), patch.object(gate, "evaluate_entry_reconciliation", new=_assess(available=True)):
        out = await route.evaluate_now(object())
    assert out["applies_to_current_broker"] is False


@pytest.mark.asyncio
async def test_drilling_records_nothing_and_places_nothing():
    """The drill must not write the rows observe is collecting, or submit."""
    broker = MagicMock()
    for name in ("place_order", "place_equity_order", "cancel_open_orders",
                 "cancel_all_open_orders"):
        setattr(broker, name, AsyncMock())
    with _as({"id": "ops"}), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch.object(gate, "evaluate_entry_reconciliation", new=_assess(
             available=True, findings=(("untracked_at_broker", "ZZZ"),))), \
         patch.object(gate, "_record", new=AsyncMock()) as record:
        await route.evaluate_now(object())
    record.assert_not_awaited()
    for name in ("place_order", "place_equity_order", "cancel_open_orders",
                 "cancel_all_open_orders"):
        getattr(broker, name).assert_not_awaited()


# ── wiring ─────────────────────────────────────────────────────────────────

def test_the_routes_sit_under_the_admin_prefix_and_outside_the_public_allowlist():
    """The session allowlist matches on path alone, so the prefix is the
    boundary: these must need a session. Asked of the real check, with a
    control proving it can say yes."""
    from app.api.auth_deps import is_public_path

    assert route.router.prefix == "/api/admin/entry-gate"
    assert is_public_path("/api/health") is True, "the check is not reading the real allowlist"
    assert is_public_path("/api/admin/entry-gate") is False
    assert is_public_path("/api/admin/entry-gate/records") is False


def test_the_routes_are_registered_on_the_app():
    from app.main import app

    paths = {r.path for r in app.routes}
    assert "/api/admin/entry-gate" in paths
    assert "/api/admin/entry-gate/records" in paths
