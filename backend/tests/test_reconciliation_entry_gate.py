"""The reconciliation gate for new IBKR entries: off, observe, enforce.

Written around the ways it could do harm in each direction:

  * too strict  - vetoing an entry for something that is not a real problem
                  (a position that just filled and is waiting to be promoted);
  * too lax     - calling "could not check" or "unclassifiable" clean, or
                  letting an exit be blocked, or letting observe change what an
                  entry does;
  * too slow    - an entry waiting on a stalled broker or database.

Reasons come from the REAL reconciler (only its database and the broker are
faked), so what is asserted is what the reconciler actually reports.
"""
from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import settings
from app.services import reconciliation_entry_gate as gate
from app.services.execution_interlock import EntryHalted, EntryPermit
from app.services.position_reconciler import ReconciliationResult
from test_signed_reconciliation import eq_pos, opt_pos, row


# ── a book to reconcile against ────────────────────────────────────────────

@contextlib.contextmanager
def book(positions=(), open_trades=(), pending=(), *, broker_error=None,
         db_error=None, pending_error=None, hang=False):
    """Fake only the edges: the broker's positions, the open-trade query the
    reconciler makes, and the pending-ticker query the gate makes."""
    broker = MagicMock()
    if broker_error:
        broker.get_positions = AsyncMock(side_effect=broker_error)
    else:
        broker.get_positions = AsyncMock(return_value=list(positions))

    async def execute(*a, **k):
        if hang:
            await asyncio.sleep(30)
        if db_error:
            raise db_error
        return MagicMock(scalars=MagicMock(return_value=MagicMock(
            all=MagicMock(return_value=list(open_trades)))))

    session = MagicMock()
    session.execute = execute

    async def pending_rows(org=None):
        if pending_error:
            raise pending_error
        return {p.upper() for p in pending}

    with contextlib.ExitStack() as stack:
        db = stack.enter_context(patch("app.services.position_reconciler.AsyncSessionLocal"))
        db.return_value.__aenter__ = AsyncMock(return_value=session)
        db.return_value.__aexit__ = AsyncMock(return_value=False)
        stack.enter_context(patch.object(gate, "_pending_underlyings", new=pending_rows))
        yield broker


@pytest.fixture
def mode(monkeypatch):
    def set_mode(value):
        monkeypatch.setattr(settings, "reconciliation_entry_gate_mode", value)
    return set_mode


@pytest.fixture
def record():
    with patch.object(gate, "_record", new=AsyncMock()) as rec:
        yield rec


PERMIT = EntryPermit(epoch=0)


@pytest.fixture(autouse=True)
def _no_halt(monkeypatch):
    from app.services.kill_switch import KillSwitch
    monkeypatch.setattr("app.services.kill_switch.kill_switch_service", KillSwitch())


async def run_gate(broker, permit=PERMIT, ticker="NVDA", asset="equity"):
    await gate.require_reconciled_entry(broker, permit, ticker=ticker, asset_type=asset)


# ── never gated, never evaluated ───────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("m", ["off", "observe", "enforce"])
async def test_an_exit_carries_no_permit_and_is_never_evaluated_in_any_mode(m, mode, record):
    """A close or emergency flatten must go through even when the book is a
    mess; blocking it would trap the desk in exactly the position it is trying
    to leave."""
    mode(m)
    with book(positions=[eq_pos("AAPL", -10)], open_trades=[row("AAPL", 10)]) as broker, \
         patch.object(gate, "evaluate_entry_reconciliation", new=AsyncMock()) as evaluate:
        await run_gate(broker, permit=None)
    evaluate.assert_not_awaited()
    record.assert_not_awaited()


@pytest.mark.asyncio
async def test_off_never_evaluates(mode, record):
    mode("off")
    with book(positions=[eq_pos("AAPL", -10)], open_trades=[row("AAPL", 10)]) as broker, \
         patch.object(gate, "evaluate_entry_reconciliation", new=AsyncMock()) as evaluate:
        await run_gate(broker)
    evaluate.assert_not_awaited()
    record.assert_not_awaited()


# ── clean ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("m", ["observe", "enforce"])
async def test_a_clean_book_passes_and_writes_nothing(m, mode, record):
    mode(m)
    with book(positions=[eq_pos("AAPL", 10)], open_trades=[row("AAPL", 10)]) as broker:
        await run_gate(broker)
    record.assert_not_awaited()


# ── known mismatches ───────────────────────────────────────────────────────

KNOWN = {
    "untracked": (dict(positions=[eq_pos("ZZZ", 5)]), "untracked_at_broker:ZZZ"),
    "phantom": (dict(open_trades=[row("GONE", 5)]), "phantom_in_db:GONE"),
    "quantity": (dict(positions=[eq_pos("AAPL", 10)], open_trades=[row("AAPL", 7)]),
                 "quantity_mismatch:AAPL"),
    "direction": (dict(positions=[eq_pos("AAPL", -10)], open_trades=[row("AAPL", 10)]),
                  "direction_mismatch:AAPL"),
    "unverified": (dict(positions=[eq_pos("AAPL", -10)],
                        open_trades=[row("AAPL", 10, "equity_buy")]),
                   "direction_unverified:AAPL"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", KNOWN)
async def test_observe_reports_every_known_mismatch_and_blocks_nothing(name, mode, record):
    kwargs, expected = KNOWN[name]
    mode("observe")
    with book(**kwargs) as broker:
        await run_gate(broker, ticker="NVDA", asset="equity")      # must not raise
    record.assert_awaited_once()
    m, status, assessment, ticker, asset = record.await_args.args
    assert (m, status, ticker, asset) == ("observe", "would_veto", "NVDA", "equity")
    assert expected in assessment.reasons()
    assert assessment.available is True and assessment.reason == "reconciliation_unresolved"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", KNOWN)
async def test_enforce_vetoes_every_known_mismatch_with_a_stable_reason(name, mode, record):
    kwargs, expected = KNOWN[name]
    mode("enforce")
    with book(**kwargs) as broker:
        with pytest.raises(gate.ReconciliationBlocked) as exc:
            await run_gate(broker)
    assert str(exc.value) == "reconciliation_unresolved"
    assert isinstance(exc.value, EntryHalted)      # so the route already handles it
    assert record.await_args.args[1] == "vetoed"
    assert expected in record.await_args.args[2].reasons()


@pytest.mark.asyncio
async def test_a_record_names_the_scope_and_what_it_found(mode):
    """The persisted record, not just the call: scope, ticker and reasons."""
    mode("observe")
    saved = {}

    class Sess:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def begin(self): return self
        def add(self, obj): saved["event"] = obj

    with book(positions=[eq_pos("ZZZ", 5)]) as broker, \
         patch("app.core.database.AsyncSessionLocal", return_value=Sess()):
        await run_gate(broker, ticker="NVDA", asset="options")
    event = saved["event"]
    assert (event.kind, event.status, event.ticker, event.asset_type) == (
        "entry_gate", "would_veto", "NVDA", "options")
    assert event.payload["scope"] == "single-operator"
    assert event.payload["mode"] == "observe"
    assert event.payload["reasons"] == ["untracked_at_broker:ZZZ"]
    assert event.payload["explained"] == {} and event.payload["unavailable_cause"] is None


# ── could not check ────────────────────────────────────────────────────────

UNAVAILABLE = {
    "broker": (dict(broker_error=ConnectionError("gateway down")), "broker_unavailable"),
    "database": (dict(db_error=RuntimeError("db down")), "db_unavailable"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", UNAVAILABLE)
async def test_observe_says_when_it_could_not_check_and_blocks_nothing(name, mode, record):
    kwargs, cause = UNAVAILABLE[name]
    mode("observe")
    with book(**kwargs) as broker:
        await run_gate(broker)
    status, assessment = record.await_args.args[1], record.await_args.args[2]
    assert status == "unavailable" and assessment.unavailable_cause == cause
    assert assessment.available is False and assessment.reasons() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("name", UNAVAILABLE)
async def test_enforce_treats_could_not_check_as_a_veto_not_as_clean(name, mode, record):
    kwargs, cause = UNAVAILABLE[name]
    mode("enforce")
    with book(**kwargs) as broker:
        with pytest.raises(gate.ReconciliationBlocked) as exc:
            await run_gate(broker)
    assert str(exc.value) == "reconciliation_unavailable"
    assert record.await_args.args[2].unavailable_cause == cause


@pytest.mark.asyncio
async def test_a_stalled_check_is_cut_off_at_the_bound_in_both_modes(mode, record, monkeypatch):
    monkeypatch.setattr(gate, "GATE_TIMEOUT_SECONDS", 0.05)
    loop = asyncio.get_running_loop()
    for m in ("observe", "enforce"):
        mode(m)
        with book(hang=True) as broker:
            started = loop.time()
            try:
                await run_gate(broker)
            except gate.ReconciliationBlocked as exc:
                assert m == "enforce" and str(exc) == "reconciliation_unavailable"
            assert loop.time() - started < 2.0, f"{m} waited on a stalled check"
        assert record.await_args.args[2].unavailable_cause == "timeout"


@pytest.mark.asyncio
async def test_an_unexpected_error_is_unavailable_not_clean_and_not_a_crash(mode, record):
    mode("observe")
    with book() as broker, patch(
        "app.services.position_reconciler.PositionReconciler.check",
        new=AsyncMock(side_effect=ValueError("boom")),
    ):
        await run_gate(broker)           # observe: must not raise into the entry
    assessment = record.await_args.args[2]
    assert assessment.available is False and assessment.unavailable_cause == "error:ValueError"


@pytest.mark.asyncio
async def test_an_unclean_result_nothing_can_explain_is_never_read_as_clean(mode, record):
    """Fail closed: unclean with no list entry and no recognised warning."""
    mode("enforce")
    odd = ReconciliationResult(
        clean=False, broker_position_count=0, db_open_trade_count=0,
        untracked_at_broker=[], phantom_in_db=[], warnings=["something new"],
    )
    with book() as broker, patch(
        "app.services.position_reconciler.PositionReconciler.check",
        new=AsyncMock(return_value=odd),
    ):
        with pytest.raises(gate.ReconciliationBlocked):
            await run_gate(broker)
    assert "unclassified" in record.await_args.args[2].reasons()


# ── recovery: neither verdict is sticky ────────────────────────────────────

@pytest.mark.asyncio
async def test_enforce_recovers_when_the_broker_comes_back(mode, record):
    mode("enforce")
    with book(broker_error=ConnectionError("down")) as broker:
        with pytest.raises(gate.ReconciliationBlocked):
            await run_gate(broker)
    with book(positions=[eq_pos("AAPL", 10)], open_trades=[row("AAPL", 10)]) as broker:
        await run_gate(broker)           # the same process, a healthy broker: passes


@pytest.mark.asyncio
async def test_enforce_recovers_when_a_mismatch_is_resolved(mode, record):
    mode("enforce")
    with book(positions=[eq_pos("AAPL", -10)], open_trades=[row("AAPL", 10)]) as broker:
        with pytest.raises(gate.ReconciliationBlocked):
            await run_gate(broker)
    with book(positions=[eq_pos("AAPL", -10)], open_trades=[row("AAPL", 10, "equity_short")]) as broker:
        await run_gate(broker)


# ── a position that just filled is explained, not vetoed ───────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("m", ["observe", "enforce"])
async def test_a_filled_position_waiting_to_be_promoted_is_not_a_finding(m, mode, record):
    """The comparison reads only OPEN rows; a working order's row is pending.
    Without this, an enforcing gate would veto every entry for up to a fill-poll
    cycle after any fill."""
    mode(m)
    with book(positions=[eq_pos("NVDA", 10)], pending=["NVDA"]) as broker:
        await run_gate(broker, ticker="AAPL")
    record.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_explanation_is_kept_so_it_can_be_audited():
    with book(positions=[eq_pos("NVDA", 10)], pending=["nvda"]) as broker:
        assessment = await gate.evaluate_entry_reconciliation(broker)
    assert assessment.clean is True
    assert assessment.explained == {"NVDA": "untracked_at_broker: pending_trade"}


@pytest.mark.asyncio
async def test_a_partial_fill_against_a_pending_entry_is_explained():
    with book(positions=[eq_pos("NVDA", 4)], open_trades=[row("NVDA", 10)],
              pending=["NVDA"]) as broker:
        assessment = await gate.evaluate_entry_reconciliation(broker)
    assert assessment.clean is True
    assert assessment.explained == {"NVDA": "quantity_mismatch: pending_trade"}


@pytest.mark.asyncio
async def test_a_pending_row_on_one_ticker_explains_nothing_on_another():
    with book(positions=[eq_pos("NVDA", 10), eq_pos("ZZZ", 5)], pending=["NVDA"]) as broker:
        assessment = await gate.evaluate_entry_reconciliation(broker)
    assert assessment.reasons() == ["untracked_at_broker:ZZZ"]
    assert list(assessment.explained) == ["NVDA"]


@pytest.mark.asyncio
async def test_a_pending_row_never_explains_a_flipped_position():
    with book(positions=[eq_pos("AAPL", -10)], open_trades=[row("AAPL", 10)],
              pending=["AAPL"]) as broker:
        assessment = await gate.evaluate_entry_reconciliation(broker)
    assert assessment.reasons() == ["direction_mismatch:AAPL"] and assessment.explained == {}


@pytest.mark.asyncio
async def test_a_pending_row_never_explains_a_phantom_row():
    with book(open_trades=[row("GONE", 5)], pending=["GONE"]) as broker:
        assessment = await gate.evaluate_entry_reconciliation(broker)
    assert assessment.reasons() == ["phantom_in_db:GONE"]


@pytest.mark.asyncio
async def test_if_pending_rows_cannot_be_read_nothing_is_assumed_explained(mode, record):
    mode("enforce")
    with book(positions=[eq_pos("NVDA", 10)], pending_error=RuntimeError("db")) as broker:
        with pytest.raises(gate.ReconciliationBlocked) as exc:
            await run_gate(broker)
    assert str(exc.value) == "reconciliation_unavailable"
    assert record.await_args.args[2].unavailable_cause == "db_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [
    dict(positions=[eq_pos("AAPL", 10)], open_trades=[row("AAPL", 10)]),          # clean
    dict(open_trades=[row("GONE", 5)]),                                           # phantom only
    dict(positions=[eq_pos("AAPL", -10)], open_trades=[row("AAPL", 10)]),         # flip only
], ids=["clean", "phantom-only", "flip-only"])
async def test_pending_rows_are_only_read_when_a_finding_could_be_explained_by_one(kwargs):
    """A clean book, a phantom row and a flipped position are things a pending
    row cannot account for, so asking the database about pending rows would be
    a wasted read on the entry path."""
    queried = []

    async def pending_rows(org=None):
        queried.append(1)
        return set()

    with book(**kwargs) as broker, patch.object(gate, "_pending_underlyings", new=pending_rows):
        await gate.evaluate_entry_reconciliation(broker)
    assert queried == []


@pytest.mark.asyncio
async def test_pending_rows_are_read_when_an_untracked_position_might_be_one():
    """The positive control: the lookup does happen when it can matter."""
    queried = []

    async def pending_rows(org=None):
        queried.append(1)
        return set()

    with book(positions=[eq_pos("ZZZ", 5)]) as broker, \
         patch.object(gate, "_pending_underlyings", new=pending_rows):
        await gate.evaluate_entry_reconciliation(broker)
    assert queried == [1]


# ── observe cannot change what an entry does ───────────────────────────────

@pytest.mark.asyncio
async def test_a_failed_record_write_never_affects_the_entry(mode):
    mode("observe")
    with book(positions=[eq_pos("ZZZ", 5)]) as broker, \
         patch("app.core.database.AsyncSessionLocal", side_effect=RuntimeError("db write")):
        await run_gate(broker)           # swallowed: the entry proceeds


@pytest.mark.asyncio
@pytest.mark.parametrize("m", ["observe", "enforce"])
async def test_a_halt_during_the_check_still_invalidates_the_send(m, mode, record):
    """Whatever the verdict, a kill switch engaged while the gate was waiting
    means this entry must not go."""
    from app.services import kill_switch as ks
    mode(m)
    switch = ks.kill_switch_service

    async def positions_then_halt():
        switch._engaged = True
        switch._entry_epoch += 1
        return []

    with book() as broker:
        broker.get_positions = positions_then_halt
        with pytest.raises(EntryHalted) as exc:
            await run_gate(broker, permit=EntryPermit(epoch=switch.entry_epoch))
    assert str(exc.value) == "kill_switch"


@pytest.mark.asyncio
async def test_a_permit_already_halted_is_refused_before_anything_is_read(mode):
    from app.services import kill_switch as ks
    mode("observe")
    ks.kill_switch_service._entry_epoch += 1
    with book() as broker, patch.object(
        gate, "evaluate_entry_reconciliation", new=AsyncMock()
    ) as evaluate:
        with pytest.raises(EntryHalted):
            await run_gate(broker, permit=EntryPermit(epoch=0))
    evaluate.assert_not_awaited()


# ── it only ever compares the single operator's book ───────────────────────

@pytest.mark.asyncio
async def test_the_reconciler_is_built_for_the_single_operator_scope_only():
    seen = {}

    class Spy:
        def __init__(self, broker, *, organization_id):
            seen["org"] = organization_id

        async def check(self):
            return ReconciliationResult(
                clean=True, broker_position_count=0, db_open_trade_count=0,
                untracked_at_broker=[], phantom_in_db=[],
            )

    with patch("app.services.position_reconciler.PositionReconciler", Spy):
        assert (await gate.evaluate_entry_reconciliation(MagicMock())).clean is True
    assert seen == {"org": None}


@pytest.mark.asyncio
async def test_option_exposure_is_still_compared_by_the_size_check():
    with book(positions=[opt_pos("SPY", -2)],
              open_trades=[row("SPY", 1, spread_type="put", strategy="bull_put_spread")]) as broker:
        assessment = await gate.evaluate_entry_reconciliation(broker)
    assert assessment.reasons() == ["quantity_mismatch:SPY"]


# ── whose book: resolved the way the periodic sweep resolves it ────────────

@pytest.mark.asyncio
async def test_the_reconciler_is_built_for_the_designated_operator_organization(monkeypatch):
    """With OPERATOR_ORGANIZATION_ID set, the operator's book is that
    organization's, not the unattributed rows alone."""
    import uuid
    org = uuid.UUID("aaaaaaaa-0000-4000-8000-0000000000c1")
    monkeypatch.setattr(settings, "operator_organization_id", str(org))
    seen = {}

    class Spy:
        def __init__(self, broker, *, organization_id):
            seen["org"] = organization_id

        async def check(self):
            return ReconciliationResult(
                clean=True, broker_position_count=0, db_open_trade_count=0,
                untracked_at_broker=[], phantom_in_db=[],
            )

    with patch("app.services.position_reconciler.PositionReconciler", Spy):
        assessment = await gate.evaluate_entry_reconciliation(MagicMock())
    assert seen == {"org": org} and assessment.scope == str(org)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", ["", "   ", "not-a-uuid"])
async def test_an_unset_or_unusable_designation_means_the_unattributed_scope(raw, monkeypatch):
    monkeypatch.setattr(settings, "operator_organization_id", raw)
    assert gate.operator_scope() is None and gate.scope_label(None) == "single-operator"


@pytest.mark.asyncio
async def test_the_pending_lookup_receives_the_same_scope_as_the_reconciler(monkeypatch):
    import uuid
    org = uuid.UUID("aaaaaaaa-0000-4000-8000-0000000000c1")
    monkeypatch.setattr(settings, "operator_organization_id", str(org))
    received = []

    async def pending_rows(o=None):
        received.append(o)
        return set()

    with book(positions=[eq_pos("ZZZ", 5)]) as broker, \
         patch.object(gate, "_pending_underlyings", new=pending_rows):
        await gate.evaluate_entry_reconciliation(broker)
    assert received == [org]


@pytest.mark.asyncio
async def test_every_outcome_carries_the_scope_it_was_made_in(monkeypatch):
    import uuid
    org = uuid.UUID("bbbbbbbb-0000-4000-8000-0000000000c2")
    monkeypatch.setattr(settings, "operator_organization_id", str(org))
    monkeypatch.setattr(gate, "GATE_TIMEOUT_SECONDS", 0.05)
    with book(broker_error=ConnectionError("down")) as broker:
        assert (await gate.evaluate_entry_reconciliation(broker)).scope == str(org)
    with book(hang=True) as broker:
        assert (await gate.evaluate_entry_reconciliation(broker)).scope == str(org)
    with book(positions=[eq_pos("ZZZ", 5)]) as broker:
        assert (await gate.evaluate_entry_reconciliation(broker)).scope == str(org)


@pytest.mark.asyncio
async def test_if_the_scope_cannot_be_resolved_the_check_is_unavailable_not_clean(monkeypatch):
    def boom():
        raise RuntimeError("settings unreadable")

    monkeypatch.setattr(gate, "operator_scope", boom)
    assessment = await gate.evaluate_entry_reconciliation(MagicMock())
    assert assessment.available is False
    assert assessment.unavailable_cause == "error:RuntimeError"
    assert assessment.clean is False


# ── the setting ────────────────────────────────────────────────────────────

def test_the_default_is_observe_not_off_and_not_enforce():
    from app.core.config import Settings
    assert Settings().reconciliation_entry_gate_mode == "observe"


@pytest.mark.parametrize("raw, expected", [
    ("enforce", "enforce"), ("ENFORCE", "enforce"), (" Observe ", "observe"), ("OFF", "off"),
])
def test_the_mode_is_case_insensitive(raw, expected, monkeypatch):
    from app.core.config import Settings
    monkeypatch.setenv("RECONCILIATION_ENTRY_GATE_MODE", raw)
    assert Settings().reconciliation_entry_gate_mode == expected


@pytest.mark.parametrize("bad", ["enfroce", "on", "true", "", "1"])
def test_a_value_that_is_not_a_mode_fails_startup_instead_of_meaning_off(bad, monkeypatch):
    from pydantic import ValidationError
    from app.core.config import Settings
    monkeypatch.setenv("RECONCILIATION_ENTRY_GATE_MODE", bad)
    with pytest.raises(ValidationError):
        Settings()
