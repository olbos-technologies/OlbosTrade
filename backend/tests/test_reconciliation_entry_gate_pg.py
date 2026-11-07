"""The entry gate against real rows: the pending lookup and the durable record.

Two things are decided by the database and cannot be shown with a mock, which
returns whatever it is told:

  * which rows count as a PENDING entry for the single-operator book - an
    organization's pending entry must not explain a position in the operator's
    account, and an open, closed or cancelled row explains nothing;
  * that what observe records actually survives, with its scope and reasons,
    because that record is the whole point of observing.
"""
from __future__ import annotations

from sqlalchemy import select, text

import pytest
import pytest_asyncio

from app.models.execution_event import ExecutionEvent
from app.services import reconciliation_entry_gate as gate
from app.services.execution_interlock import EntryPermit
from test_signed_reconciliation import eq_pos
from test_worker_scope_pg import (  # noqa: F401  (db is a fixture)
    ORG_A, ORG_B, _seed, db, pytestmark,
)
from unittest.mock import AsyncMock, MagicMock


@pytest_asyncio.fixture
async def gate_db(db):
    """The shared fixture's tables plus execution_events, which `_record` writes."""
    engine = db.kw["bind"]
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS execution_events"))
        await conn.run_sync(ExecutionEvent.__table__.create)
    yield db
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS execution_events"))


def _broker(positions=()):
    b = MagicMock()
    b.get_positions = AsyncMock(return_value=list(positions))
    return b


async def _events(db):
    async with db() as s:
        return (await s.execute(
            select(ExecutionEvent).where(ExecutionEvent.kind == "entry_gate")
        )).scalars().all()


# ── which rows count as pending ────────────────────────────────────────────

async def test_only_the_single_operators_pending_rows_are_returned(gate_db):
    await _seed(gate_db, None, "NVDA", status="pending")
    await _seed(gate_db, None, "msft", status="pending")
    await _seed(gate_db, None, "OPENED", status="open")
    await _seed(gate_db, None, "CLOSED", status="closed")
    await _seed(gate_db, None, "DEAD", status="cancelled")
    await _seed(gate_db, ORG_A, "THEIRS", status="pending")
    assert await gate._pending_underlyings() == {"NVDA", "MSFT"}


async def test_no_pending_rows_is_an_empty_set_not_an_error(gate_db):
    assert await gate._pending_underlyings() == set()


# ── the lookup, through the real reconciler ────────────────────────────────

async def test_a_filled_position_with_a_pending_row_is_explained(gate_db):
    await _seed(gate_db, None, "NVDA", status="pending")
    assessment = await gate.evaluate_entry_reconciliation(_broker([eq_pos("NVDA", 10)]))
    assert assessment.clean is True
    assert assessment.explained == {"NVDA": "untracked_at_broker: pending_trade"}


async def test_another_organizations_pending_row_does_not_explain_the_operators_position(gate_db):
    await _seed(gate_db, ORG_A, "NVDA", status="pending")
    assessment = await gate.evaluate_entry_reconciliation(_broker([eq_pos("NVDA", 10)]))
    assert assessment.reasons() == ["untracked_at_broker:NVDA"]
    assert assessment.explained == {}


@pytest.mark.parametrize("status", ["closed", "cancelled"])
async def test_a_finished_row_explains_nothing(gate_db, status):
    await _seed(gate_db, None, "NVDA", status=status)
    assessment = await gate.evaluate_entry_reconciliation(_broker([eq_pos("NVDA", 10)]))
    assert assessment.reasons() == ["untracked_at_broker:NVDA"]


async def test_a_tracked_position_is_clean_with_nothing_explained(gate_db):
    await _seed(gate_db, None, "NVDA", status="open", qty=10)
    assessment = await gate.evaluate_entry_reconciliation(_broker([eq_pos("NVDA", 10)]))
    assert assessment.clean is True and assessment.explained == {}


# ── the durable record ─────────────────────────────────────────────────────

async def test_observe_leaves_a_record_that_survives_with_scope_and_reasons(gate_db, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "reconciliation_entry_gate_mode", "observe")
    from app.services.kill_switch import KillSwitch
    monkeypatch.setattr("app.services.kill_switch.kill_switch_service", KillSwitch())

    await gate.require_reconciled_entry(
        _broker([eq_pos("ZZZ", 5)]), EntryPermit(epoch=0), ticker="NVDA", asset_type="equity",
    )
    (event,) = await _events(gate_db)
    assert (event.kind, event.status, event.ticker, event.asset_type) == (
        "entry_gate", "would_veto", "NVDA", "equity")
    assert event.payload["scope"] == "single-operator"
    assert event.payload["mode"] == "observe"
    assert event.payload["reasons"] == ["untracked_at_broker:ZZZ"]
    assert event.payload["available"] is True and event.payload["unavailable_cause"] is None
    assert event.payload["elapsed_ms"] >= 0 and event.created_at is not None


async def test_enforce_records_the_veto_it_raised(gate_db, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "reconciliation_entry_gate_mode", "enforce")
    from app.services.kill_switch import KillSwitch
    monkeypatch.setattr("app.services.kill_switch.kill_switch_service", KillSwitch())

    with pytest.raises(gate.ReconciliationBlocked):
        await gate.require_reconciled_entry(
            _broker([eq_pos("ZZZ", 5)]), EntryPermit(epoch=0), ticker="NVDA", asset_type="options",
        )
    (event,) = await _events(gate_db)
    assert (event.status, event.asset_type) == ("vetoed", "options")
    assert event.payload["mode"] == "enforce"


async def test_an_unreadable_broker_is_recorded_as_unavailable_with_its_cause(gate_db, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "reconciliation_entry_gate_mode", "observe")
    from app.services.kill_switch import KillSwitch
    monkeypatch.setattr("app.services.kill_switch.kill_switch_service", KillSwitch())

    broker = MagicMock()
    broker.get_positions = AsyncMock(side_effect=ConnectionError("gateway down"))
    await gate.require_reconciled_entry(
        broker, EntryPermit(epoch=0), ticker="NVDA", asset_type="equity",
    )
    (event,) = await _events(gate_db)
    assert event.status == "unavailable"
    assert event.payload["available"] is False
    assert event.payload["unavailable_cause"] == "broker_unavailable"
    assert event.payload["reasons"] == []


async def test_a_clean_check_writes_no_record(gate_db, monkeypatch):
    """Volume: one row per would-veto or unreadable check, not per entry."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "reconciliation_entry_gate_mode", "observe")
    from app.services.kill_switch import KillSwitch
    monkeypatch.setattr("app.services.kill_switch.kill_switch_service", KillSwitch())

    await _seed(gate_db, None, "NVDA", status="open", qty=10)
    await gate.require_reconciled_entry(
        _broker([eq_pos("NVDA", 10)]), EntryPermit(epoch=0), ticker="AAPL", asset_type="equity",
    )
    assert await _events(gate_db) == []


# ── the operator's view of the record ──────────────────────────────────────

async def _write_event(db, status, ticker, *, days_ago=0, payload=None):
    import datetime as dt
    async with db() as s, s.begin():
        s.add(ExecutionEvent(
            kind="entry_gate", ticker=ticker, asset_type="equity", status=status,
            payload=payload or {"mode": "observe", "scope": "single-operator",
                                "reasons": [f"untracked_at_broker:{ticker}"]},
            created_at=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago),
        ))


def _operator():
    from unittest.mock import patch
    from app.api.routes import admin_claims
    return patch.object(admin_claims, "current_user", return_value={"id": "ops"})


async def test_the_records_route_counts_by_outcome_and_lists_newest_first(gate_db):
    from app.api.routes import admin_entry_gate as route
    await _write_event(gate_db, "would_veto", "AAA", days_ago=2)
    await _write_event(gate_db, "would_veto", "BBB", days_ago=1)
    await _write_event(gate_db, "unavailable", "CCC", days_ago=0, payload={
        "mode": "observe", "scope": "single-operator", "reasons": [],
        "unavailable_cause": "broker_unavailable"})
    with _operator():
        out = await route.records(object())
    assert out["counts"] == {"would_veto": 2, "unavailable": 1}
    assert [r["ticker"] for r in out["records"]] == ["CCC", "BBB", "AAA"]
    newest = out["records"][0]
    assert newest["unavailable_cause"] == "broker_unavailable" and newest["scope"] == "single-operator"
    assert out["records"][1]["reasons"] == ["untracked_at_broker:BBB"]


async def test_the_records_route_leaves_out_rows_older_than_the_window(gate_db):
    from app.api.routes import admin_entry_gate as route
    await _write_event(gate_db, "would_veto", "OLD", days_ago=30)
    await _write_event(gate_db, "would_veto", "NEW", days_ago=1)
    with _operator():
        week = await route.records(object(), days=7)
        quarter = await route.records(object(), days=60)
    assert [r["ticker"] for r in week["records"]] == ["NEW"] and week["counts"] == {"would_veto": 1}
    assert {r["ticker"] for r in quarter["records"]} == {"OLD", "NEW"}


async def test_the_records_route_ignores_other_kinds_of_event(gate_db):
    from app.api.routes import admin_entry_gate as route
    async with gate_db() as s, s.begin():
        s.add(ExecutionEvent(kind="claim_resolved", ticker="XXX", status="released_by_evidence",
                             payload={}))
    await _write_event(gate_db, "would_veto", "AAA")
    with _operator():
        out = await route.records(object())
    assert [r["ticker"] for r in out["records"]] == ["AAA"]


async def test_the_records_route_limits_how_many_rows_it_returns(gate_db):
    from app.api.routes import admin_entry_gate as route
    for i in range(5):
        await _write_event(gate_db, "would_veto", f"T{i}")
    with _operator():
        out = await route.records(object(), limit=2)
        floor = await route.records(object(), limit=-5)
    assert len(out["records"]) == 2
    assert len(floor["records"]) == 1                 # clamped to at least one
    assert out["counts"] == {"would_veto": 5}         # counts are not limited


async def test_an_empty_record_is_an_empty_answer_not_an_error(gate_db):
    from app.api.routes import admin_entry_gate as route
    with _operator():
        out = await route.records(object())
    assert out["counts"] == {} and out["records"] == []


# ── whose book: the operator's, exactly as the periodic sweep sees it ───────
#
# The first version of the gate reconciled against unattributed rows only. A
# desk whose own open positions are attributed to the designated operator
# organization then saw every one of them as untracked, and a live drill
# against production reported all three open positions that way. The tests
# above never used that shape, which is why they passed.

@pytest.fixture
def designated(monkeypatch):
    """Designate ORG_A as the operator organization, as production does."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "operator_organization_id", str(ORG_A))
    return ORG_A


async def test_positions_attributed_to_the_designated_operator_are_tracked(gate_db, designated):
    """The live failure, exactly: three open rows owned by the operator's
    organization, the same three positions at the broker."""
    for ticker, qty in (("AMZN", 7), ("FER", 337), ("SPCX", 18)):
        await _seed(gate_db, ORG_A, ticker, status="open", qty=qty)
    book = [eq_pos("AMZN", 7), eq_pos("FER", 337), eq_pos("SPCX", 18)]
    assessment = await gate.evaluate_entry_reconciliation(_broker(book))
    assert assessment.reasons() == [] and assessment.clean is True
    assert assessment.scope == str(ORG_A)


async def test_unattributed_rows_are_also_the_designated_operators(gate_db, designated):
    """The sweep treats unattributed rows and the designated operator as ONE
    scope, because they share one broker."""
    await _seed(gate_db, None, "LEGACY", status="open", qty=10)
    await _seed(gate_db, ORG_A, "NEWER", status="open", qty=5)
    assessment = await gate.evaluate_entry_reconciliation(
        _broker([eq_pos("LEGACY", 10), eq_pos("NEWER", 5)]))
    assert assessment.clean is True


async def test_another_organizations_row_does_not_track_the_operators_position(gate_db, designated):
    await _seed(gate_db, ORG_B, "AMZN", status="open", qty=7)
    assessment = await gate.evaluate_entry_reconciliation(_broker([eq_pos("AMZN", 7)]))
    assert assessment.reasons() == ["untracked_at_broker:AMZN"]


async def test_without_a_designation_rows_attributed_to_an_organization_are_not_the_operators(gate_db):
    """The strict side of the same rule: undesignated, the operator's book is
    the unattributed rows, and an organization's row is somebody else's."""
    await _seed(gate_db, ORG_A, "AMZN", status="open", qty=7)
    assessment = await gate.evaluate_entry_reconciliation(_broker([eq_pos("AMZN", 7)]))
    assert assessment.reasons() == ["untracked_at_broker:AMZN"]
    assert assessment.scope == "single-operator"


async def test_pending_rows_are_looked_up_in_the_designated_operators_scope(gate_db, designated):
    await _seed(gate_db, ORG_A, "MINE", status="pending")
    await _seed(gate_db, None, "LEGACY", status="pending")
    await _seed(gate_db, ORG_B, "THEIRS", status="pending")
    assert await gate._pending_underlyings(ORG_A) == {"MINE", "LEGACY"}
    assert await gate._pending_underlyings(None) == {"LEGACY"}


async def test_a_fresh_fill_on_a_designated_operator_is_explained_not_flagged(gate_db, designated):
    await _seed(gate_db, ORG_A, "NVDA", status="pending")
    assessment = await gate.evaluate_entry_reconciliation(_broker([eq_pos("NVDA", 10)]))
    assert assessment.clean is True
    assert assessment.explained == {"NVDA": "untracked_at_broker: pending_trade"}


async def test_the_record_names_the_designated_scope(gate_db, designated, monkeypatch):
    from app.core.config import settings
    from app.services.kill_switch import KillSwitch
    monkeypatch.setattr(settings, "reconciliation_entry_gate_mode", "observe")
    monkeypatch.setattr("app.services.kill_switch.kill_switch_service", KillSwitch())
    await gate.require_reconciled_entry(
        _broker([eq_pos("ZZZ", 5)]), EntryPermit(epoch=0), ticker="NVDA", asset_type="equity",
    )
    (event,) = await _events(gate_db)
    assert event.payload["scope"] == str(ORG_A)
