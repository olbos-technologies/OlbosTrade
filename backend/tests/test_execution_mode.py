"""Tests for the ExecutionModeManager (Manual / Copilot / Autopilot + persistence).

Previously mode was persisted to /tmp/olbostrade_exec_mode.json inside the
container — wiped on every deploy/restart. Now it's a DB-backed row in
execution_events (kind="mode_change"), mirroring the kill switch's rehydrate()
pattern (kill_switch.py). These tests mock AsyncSessionLocal the same way
test_kill_switch.py does.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.execution_mode import ExecutionMode, ExecutionModeManager


def _session(execute_result=None):
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    txn = MagicMock()
    txn.__aenter__ = AsyncMock(return_value=txn)
    txn.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=txn)
    session.add = MagicMock()
    if execute_result is not None:
        session.execute = AsyncMock(return_value=execute_result)
    return session


def test_enum_values():
    assert ExecutionMode.MANUAL.value == "manual"
    assert ExecutionMode.COPILOT.value == "copilot"
    assert ExecutionMode.AUTOPILOT.value == "autopilot"


def test_default_mode_before_rehydrate():
    mgr = ExecutionModeManager()
    assert mgr.mode == ExecutionMode.MANUAL


@pytest.mark.asyncio
async def test_set_mode_and_summary_flags():
    mgr = ExecutionModeManager()
    session = _session()
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        s = await mgr.set_mode(ExecutionMode.AUTOPILOT)
    assert s["mode"] == "autopilot"
    assert s["auto_equity"] is True and s["auto_options"] is True
    assert s["needs_approval"] is False
    assert "description" in s and s["changed_by"] == "user"

    with patch("app.core.database.AsyncSessionLocal", return_value=_session()):
        s2 = await mgr.set_mode(ExecutionMode.COPILOT, by="tester")
    assert s2["needs_approval"] is True and s2["auto_equity"] is False
    assert s2["changed_by"] == "tester"

    s3 = mgr.summary()
    assert s3["mode"] == "copilot"


@pytest.mark.asyncio
async def test_set_mode_persists_row():
    mgr = ExecutionModeManager()
    session = _session()
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        await mgr.set_mode(ExecutionMode.AUTOPILOT, by="tester")
    session.add.assert_called_once()
    row = session.add.call_args.args[0]
    assert row.kind == "mode_change"
    assert row.status == "autopilot"
    assert row.payload["changed_by"] == "tester"


@pytest.mark.asyncio
async def test_escalation_is_refused_when_the_decision_cannot_be_recorded():
    """Was test_set_mode_persist_failure_is_non_fatal, which asserted that a
    failed write still changed the mode — the defect, written down as a
    requirement. A database outage used to produce a live Autopilot and an
    ordinary success response, trading automatically on a decision nothing
    held. Raising automation now requires the record to exist first.
    """
    mgr = ExecutionModeManager()
    with patch("app.core.database.AsyncSessionLocal", side_effect=Exception("db down")):
        out = await mgr.set_mode(ExecutionMode.AUTOPILOT)   # must not raise

    assert mgr.mode is ExecutionMode.MANUAL, "automation engaged without a record"
    assert out["mode"] == "manual"
    assert out["requested_mode"] == "autopilot"
    assert out["persistence"] == "unavailable"
    assert out["persisted"] is False
    assert "autopilot" in out["detail"].lower()


@pytest.mark.asyncio
async def test_a_reduction_takes_effect_even_when_the_database_is_down():
    """The opposite sign of the same rule. Making a safety reduction wait for
    a database that may be the broken thing would be the same mistake."""
    mgr = ExecutionModeManager()
    mgr._mode = ExecutionMode.AUTOPILOT
    with patch("app.core.database.AsyncSessionLocal", side_effect=Exception("db down")):
        out = await mgr.set_mode(ExecutionMode.MANUAL)

    assert mgr.mode is ExecutionMode.MANUAL, "a stop was blocked by an outage"
    assert out["mode"] == "manual"
    assert out["persistence"] == "unconfirmed"
    assert out["unconfirmed_reduction"] == "manual"
    assert out["persisted"] is False


@pytest.mark.asyncio
async def test_a_recorded_escalation_reports_confirmed():
    mgr = ExecutionModeManager()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session()):
        out = await mgr.set_mode(ExecutionMode.AUTOPILOT, by="tester")
    assert mgr.mode is ExecutionMode.AUTOPILOT
    assert out["persistence"] == "confirmed" and out["persisted"] is True
    assert "requested_mode" not in out


@pytest.mark.asyncio
async def test_rehydrate_does_not_silently_resume_autopilot():
    from datetime import datetime, timezone
    row = MagicMock(status="autopilot", created_at=datetime.now(timezone.utc),
                     payload={"changed_by": "tester"})
    result = MagicMock(scalar_one_or_none=MagicMock(return_value=row))
    mgr = ExecutionModeManager()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(result)):
        await mgr.rehydrate()
    # Autopilot is deliberately NOT auto-restored: a reduction out of it that
    # failed to persist leaves the older, more permissive row newest on disk,
    # and restoring it would hand automation back silently as a side effect of
    # a restart. Copilot keeps every signal and still asks a human first.
    assert mgr.mode == ExecutionMode.COPILOT
    assert mgr.summary()["changed_by"] == "tester"
    assert mgr.summary()["persistence"] == "stale"
    assert "re-engage" in mgr.summary()["restore_note"].lower()


@pytest.mark.asyncio
async def test_rehydrate_defaults_to_manual_when_no_history():
    result = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    mgr = ExecutionModeManager()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(result)):
        await mgr.rehydrate()
    assert mgr.mode == ExecutionMode.MANUAL


@pytest.mark.asyncio
async def test_rehydrate_failure_defaults_to_manual():
    mgr = ExecutionModeManager()
    with patch("app.core.database.AsyncSessionLocal", side_effect=Exception("db down")):
        await mgr.rehydrate()   # must not raise
    assert mgr.mode == ExecutionMode.MANUAL
