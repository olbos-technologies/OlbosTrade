"""Tests for the ExecutionModeManager (Manual / Copilot / Autopilot + persistence).

Previously mode was persisted to /tmp/olbostrade_exec_mode.json inside the
container — wiped on every deploy/restart. Now it's a DB-backed row in
execution_events (kind="mode_change"), mirroring the kill switch's rehydrate()
pattern (kill_switch.py). These tests mock AsyncSessionLocal the same way
test_kill_switch.py does.
"""

from __future__ import annotations

import asyncio
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
async def test_rehydrate_restores_autopilot_from_the_record():
    from datetime import datetime, timezone
    row = MagicMock(status="autopilot", created_at=datetime.now(timezone.utc),
                     payload={"changed_by": "tester"})
    result = MagicMock(scalar_one_or_none=MagicMock(return_value=row))
    mgr = ExecutionModeManager()
    with patch("app.core.database.AsyncSessionLocal", return_value=_session(result)):
        await mgr.rehydrate()
    # Restored as recorded. An earlier revision refused to restore Autopilot
    # at all; that closed the unpersisted-reduction hazard but disarmed
    # automation on every deploy — a cure firing on every restart for a fault
    # firing on almost none. What makes the record trustworthy instead is that
    # a reduction which fails to persist is retried until it lands
    # (test_an_unrecorded_reduction_is_retried_until_it_lands below).
    assert mgr.mode == ExecutionMode.AUTOPILOT
    assert mgr.summary()["changed_by"] == "tester"


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


# ── retrying an unrecorded reduction ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_an_unrecorded_reduction_is_retried_until_it_lands():
    """The mechanism that makes restoring from the record defensible.

    Without it, a reduction that could not be written left the newest row on
    disk more permissive than reality until somebody noticed — and a restart
    in between would resume Autopilot.
    """
    import app.services.execution_mode as em

    mgr = ExecutionModeManager()
    mgr._mode = ExecutionMode.AUTOPILOT
    calls = {"n": 0}
    session = _session()

    def _factory():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db down")
        return session

    with patch("app.core.database.AsyncSessionLocal", _factory), \
         patch.object(em, "_RETRY_INITIAL_SECONDS", 0.01), \
         patch.object(em, "_RETRY_MAX_SECONDS", 0.01):
        out = await mgr.set_mode(ExecutionMode.MANUAL)
        assert out["persistence"] == "unconfirmed"
        assert mgr.mode is ExecutionMode.MANUAL, "the reduction must be in force now"
        await asyncio.sleep(0.15)

    assert calls["n"] >= 2, "the failed write was never retried"
    assert mgr._unconfirmed_reduction is None
    assert mgr.summary()["persistence"] == "confirmed"


@pytest.mark.asyncio
async def test_a_retry_is_abandoned_when_the_mode_moves_on():
    """Re-recording a superseded reduction would make the history lie."""
    import app.services.execution_mode as em

    mgr = ExecutionModeManager()
    mgr._mode = ExecutionMode.AUTOPILOT
    attempts = {"n": 0}

    def _always_down():
        attempts["n"] += 1
        raise RuntimeError("db down")

    with patch("app.core.database.AsyncSessionLocal", _always_down), \
         patch.object(em, "_RETRY_INITIAL_SECONDS", 0.01), \
         patch.object(em, "_RETRY_MAX_SECONDS", 0.01):
        await mgr.set_mode(ExecutionMode.MANUAL)
        mgr._mode = ExecutionMode.COPILOT      # something else changed it
        await asyncio.sleep(0.1)
        settled = attempts["n"]
        await asyncio.sleep(0.1)

    assert attempts["n"] == settled, "the superseded reduction kept retrying"


@pytest.mark.asyncio
async def test_a_confirmed_change_cancels_a_pending_retry():
    import app.services.execution_mode as em

    mgr = ExecutionModeManager()
    mgr._mode = ExecutionMode.AUTOPILOT
    with patch("app.core.database.AsyncSessionLocal", side_effect=RuntimeError("db down")), \
         patch.object(em, "_RETRY_INITIAL_SECONDS", 5.0):
        await mgr.set_mode(ExecutionMode.MANUAL)
        assert mgr._retry_task is not None

    with patch("app.core.database.AsyncSessionLocal", return_value=_session()):
        await mgr.set_mode(ExecutionMode.COPILOT)

    assert mgr._retry_task is None
    assert mgr._unconfirmed_reduction is None


@pytest.mark.asyncio
async def test_no_event_loop_does_not_break_a_reduction():
    """A synchronous caller still gets the reduction; it just is not retried."""
    mgr = ExecutionModeManager()
    mgr._mode = ExecutionMode.AUTOPILOT
    with patch("app.core.database.AsyncSessionLocal", side_effect=RuntimeError("db down")), \
         patch("asyncio.get_running_loop", side_effect=RuntimeError("no loop")):
        out = await mgr.set_mode(ExecutionMode.MANUAL)
    assert mgr.mode is ExecutionMode.MANUAL
    assert out["persistence"] == "unconfirmed"
    assert mgr._retry_task is None


@pytest.mark.asyncio
async def test_giving_up_on_a_retry_is_loud(caplog):
    """Exhausting the retries leaves the stored history more permissive than
    the running mode. That is the state an operator most needs told about, so
    it is CRITICAL rather than a warning that scrolls past."""
    import logging
    import app.services.execution_mode as em

    mgr = ExecutionModeManager()
    mgr._mode = ExecutionMode.AUTOPILOT
    with caplog.at_level(logging.CRITICAL), \
         patch("app.core.database.AsyncSessionLocal", side_effect=RuntimeError("db down")), \
         patch.object(em, "_RETRY_INITIAL_SECONDS", 0.001), \
         patch.object(em, "_RETRY_MAX_SECONDS", 0.001), \
         patch.object(em, "_RETRY_ATTEMPTS", 2):
        await mgr.set_mode(ExecutionMode.MANUAL)
        await asyncio.sleep(0.1)

    assert mgr.mode is ExecutionMode.MANUAL, "the reduction stays in force regardless"
    assert any("Gave up recording" in r.getMessage() for r in caplog.records)
    assert mgr.summary()["persistence"] == "unconfirmed"
