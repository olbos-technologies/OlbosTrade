"""
The OMS order state machine (MASTER_ARCHITECTURE §7.5).

Most of these are structural: terminal states are terminal, every state is
reachable, nothing transitions to itself. The one that is not structural is
test_ambiguous_never_returns_to_submitting, and it is the reason this module
exists.
"""

from __future__ import annotations

import pytest

from app.services import order_state_machine as sm
from app.services.order_state_machine import IllegalTransition


# ── The rule that protects capital ───────────────────────────────────────────

def test_ambiguous_never_returns_to_a_dispatching_state():
    """§8.1: a transport timeout produces AMBIGUOUS, "not an automatic
    resubmission".

    AMBIGUOUS means the submission left this process and no answer came back.
    The broker may hold the order, may have filled it, may have never seen it.
    Resubmitting on that is how one intent becomes two real positions, and the
    second one is not covered by any risk check that passed for the first.

    If a future change adds this edge, this is the test that must be argued
    with rather than updated.
    """
    for target in (sm.SUBMITTING, sm.DISPATCH_QUEUED, sm.READY, sm.CREATED):
        assert not sm.can_transition(sm.AMBIGUOUS, target), (
            f"AMBIGUOUS -> {target} would resubmit an order whose outcome is "
            "unknown"
        )


def test_the_refusal_explains_itself():
    """A bare "illegal transition" in a log at 3am is not enough to act on."""
    with pytest.raises(IllegalTransition) as exc:
        sm.assert_transition(sm.AMBIGUOUS, sm.SUBMITTING)
    assert "reconciliation" in str(exc.value)
    assert "broker may already hold this order" in str(exc.value)


def test_ambiguous_is_resolved_only_by_evidence_or_a_human():
    """Reconciliation reads the broker; MANUAL_REVIEW is where it gives up."""
    assert sm.TRANSITIONS[sm.AMBIGUOUS] == frozenset({
        sm.ACKNOWLEDGED, sm.FILLED, sm.CANCELED, sm.MANUAL_REVIEW,
    })


def test_ambiguous_counts_as_possibly_live_at_the_broker():
    """"We do not know" must be treated as "maybe", or the kill switch and
    reconciliation will walk past an order that is actually working."""
    assert sm.AMBIGUOUS in sm.POSSIBLY_LIVE_AT_BROKER


# ── Terminality ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("state", sorted(sm.TERMINAL))
def test_terminal_states_have_no_outgoing_transitions(state):
    assert sm.TRANSITIONS[state] == frozenset()
    assert sm.is_terminal(state)


@pytest.mark.parametrize("state", sorted(sm.TERMINAL))
def test_nothing_leaves_a_terminal_state(state):
    for target in sorted(sm.STATES):
        assert not sm.can_transition(state, target)


def test_a_filled_order_cannot_be_resurrected():
    """The specific case: a late broker message must not reopen a closed order."""
    with pytest.raises(IllegalTransition) as exc:
        sm.assert_transition(sm.FILLED, sm.PARTIALLY_FILLED)
    assert "terminal" in str(exc.value)


# ── Structure ────────────────────────────────────────────────────────────────

def test_every_state_is_reachable_from_created():
    """An unreachable state is dead code that looks like a feature."""
    seen = {sm.INITIAL}
    frontier = [sm.INITIAL]
    while frontier:
        for nxt in sm.TRANSITIONS[frontier.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    assert seen == sm.STATES, f"unreachable from CREATED: {sorted(sm.STATES - seen)}"


def test_every_non_terminal_state_can_still_finish():
    """No state may trap an order: from anywhere, some terminal state is
    reachable. A trapped order is one no operator can close out."""
    for start in sorted(sm.STATES - sm.TERMINAL):
        seen, frontier = {start}, [start]
        while frontier and not (seen & sm.TERMINAL):
            for nxt in sm.TRANSITIONS[frontier.pop()]:
                if nxt not in seen:
                    seen.add(nxt)
                    frontier.append(nxt)
        assert seen & sm.TERMINAL, f"{start} cannot reach any terminal state"


def test_no_state_transitions_to_itself():
    """A second partial fill is a new Fill row, not a state change. A self-edge
    would make "did anything change" unanswerable from the event log."""
    for state, targets in sm.TRANSITIONS.items():
        assert state not in targets, f"{state} has a self-transition"


def test_every_transition_target_is_a_known_state():
    for state, targets in sm.TRANSITIONS.items():
        unknown = targets - sm.STATES
        assert not unknown, f"{state} points at unknown states {sorted(unknown)}"


def test_created_is_the_only_entry_point():
    """Nothing may transition INTO CREATED: an order starts there or not at all."""
    for state, targets in sm.TRANSITIONS.items():
        assert sm.CREATED not in targets, f"{state} -> CREATED re-opens an order"


# ── The happy path, and the paths that matter ────────────────────────────────

@pytest.mark.parametrize("path", [
    pytest.param([sm.CREATED, sm.RISK_APPROVED, sm.READY, sm.DISPATCH_QUEUED,
                  sm.SUBMITTING, sm.ACKNOWLEDGED, sm.FILLED], id="autopilot-fill"),
    pytest.param([sm.CREATED, sm.RISK_APPROVED, sm.APPROVAL_PENDING, sm.READY,
                  sm.DISPATCH_QUEUED, sm.SUBMITTING, sm.ACKNOWLEDGED,
                  sm.PARTIALLY_FILLED, sm.FILLED], id="copilot-partial-then-fill"),
    pytest.param([sm.CREATED, sm.BLOCKED], id="risk-blocks"),
    pytest.param([sm.CREATED, sm.RISK_APPROVED, sm.APPROVAL_PENDING, sm.REJECTED],
                 id="human-rejects"),
    pytest.param([sm.CREATED, sm.RISK_APPROVED, sm.READY, sm.DISPATCH_QUEUED,
                  sm.SUBMITTING, sm.AMBIGUOUS, sm.FILLED],
                 id="timeout-then-reconciled-as-filled"),
    pytest.param([sm.CREATED, sm.RISK_APPROVED, sm.READY, sm.DISPATCH_QUEUED,
                  sm.SUBMITTING, sm.AMBIGUOUS, sm.MANUAL_REVIEW],
                 id="timeout-unresolvable"),
    pytest.param([sm.CREATED, sm.RISK_APPROVED, sm.READY, sm.DISPATCH_QUEUED,
                  sm.SUBMITTING, sm.ACKNOWLEDGED, sm.CANCEL_PENDING, sm.CANCELED],
                 id="cancel"),
    pytest.param([sm.CREATED, sm.RISK_APPROVED, sm.READY, sm.DISPATCH_QUEUED,
                  sm.SUBMITTING, sm.ACKNOWLEDGED, sm.CANCEL_PENDING, sm.AMBIGUOUS,
                  sm.CANCELED], id="cancel-times-out-then-reconciled"),
])
def test_legal_paths(path):
    for frm, to in zip(path, path[1:]):
        sm.assert_transition(frm, to)
    assert sm.is_terminal(path[-1]), "a named path should end somewhere final"


@pytest.mark.parametrize("frm,to", [
    (sm.CREATED, sm.SUBMITTING),          # skipping risk entirely
    (sm.CREATED, sm.READY),               # skipping the risk decision
    (sm.RISK_APPROVED, sm.SUBMITTING),    # skipping the dispatch queue
    (sm.READY, sm.ACKNOWLEDGED),          # never actually submitted
    (sm.DISPATCH_QUEUED, sm.FILLED),      # filled without being sent
    (sm.APPROVAL_PENDING, sm.DISPATCH_QUEUED),
])
def test_the_risk_pipeline_cannot_be_skipped(frm, to):
    """§24 gate 3: nothing may reach a broker without passing the gates."""
    assert not sm.can_transition(frm, to)
    with pytest.raises(IllegalTransition):
        sm.assert_transition(frm, to)


# ── Input handling ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["", "filled", "FILLED ", "OPEN", "unknown"])
def test_an_unknown_state_is_rejected_not_guessed(bad):
    """§7.5: "no route or adapter may update arbitrary state strings". Note
    "filled" and "FILLED " are rejected too — a case or whitespace slip must
    not quietly become a different state."""
    with pytest.raises(IllegalTransition):
        sm.assert_transition(sm.ACKNOWLEDGED, bad)
    with pytest.raises(IllegalTransition):
        sm.is_terminal(bad)


def test_the_table_covers_every_declared_state():
    assert sm.STATES == set(sm.TRANSITIONS)
    assert sm.TERMINAL <= sm.STATES
    assert sm.POSSIBLY_LIVE_AT_BROKER <= sm.STATES
    assert sm.INITIAL in sm.STATES
