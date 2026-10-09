"""
The canonical OMS order state machine (MASTER_ARCHITECTURE §7.5).

Pure data and pure functions: no database, no broker, no I/O. The transition
table is the specification, and `can_transition` is the only thing that should
ever decide whether a move is legal — §7.5's "no route or adapter may update
arbitrary state strings" is enforceable only if there is one place to enforce
it.

THE STATE THAT MATTERS IS AMBIGUOUS. It means a submission left this process
and the broker's answer never arrived: a transport timeout, a dropped socket, a
502 from in front of the broker. The order may be live, may be filled, may not
exist. There is no transition from AMBIGUOUS back to SUBMITTING, and that
absence is the single most important thing in this file — an automatic retry on
an unknown outcome is how one intent becomes two real positions. §8.1 states it
directly: "A transport timeout after submission produces AMBIGUOUS, not an
automatic resubmission."

An ambiguous order leaves that state exactly two ways: reconciliation reads the
broker and proves what actually happened, or a human looks at it.
"""

from __future__ import annotations

from typing import Final, FrozenSet, Mapping

# ── States ───────────────────────────────────────────────────────────────────

CREATED: Final = "CREATED"
RISK_APPROVED: Final = "RISK_APPROVED"
BLOCKED: Final = "BLOCKED"
APPROVAL_PENDING: Final = "APPROVAL_PENDING"
READY: Final = "READY"
REJECTED: Final = "REJECTED"
DISPATCH_QUEUED: Final = "DISPATCH_QUEUED"
SUBMITTING: Final = "SUBMITTING"
ACKNOWLEDGED: Final = "ACKNOWLEDGED"
AMBIGUOUS: Final = "AMBIGUOUS"
PARTIALLY_FILLED: Final = "PARTIALLY_FILLED"
FILLED: Final = "FILLED"
CANCEL_PENDING: Final = "CANCEL_PENDING"
CANCELED: Final = "CANCELED"
MANUAL_REVIEW: Final = "MANUAL_REVIEW"

#: The only state an order may be created in.
INITIAL: Final = CREATED

#: Nothing leaves these. Enforced by the empty transition sets below, and
#: named separately so callers can ask "is this finished" without reasoning
#: about the table.
TERMINAL: Final[FrozenSet[str]] = frozenset({
    BLOCKED, REJECTED, FILLED, CANCELED, MANUAL_REVIEW,
})

#: States in which the broker may hold a live order. Used by the kill switch
#: and reconciliation to decide what still needs accounting for. AMBIGUOUS is
#: here precisely BECAUSE it is unknown: treating "we do not know" as "not
#: live" is how an order gets abandoned at the broker.
POSSIBLY_LIVE_AT_BROKER: Final[FrozenSet[str]] = frozenset({
    SUBMITTING, ACKNOWLEDGED, PARTIALLY_FILLED, CANCEL_PENDING, AMBIGUOUS,
})

# ── Transitions ──────────────────────────────────────────────────────────────

TRANSITIONS: Final[Mapping[str, FrozenSet[str]]] = {
    CREATED: frozenset({RISK_APPROVED, BLOCKED}),
    # The risk authority approved the economics. Whether a human must also
    # approve is a policy question, so both edges exist.
    RISK_APPROVED: frozenset({APPROVAL_PENDING, READY}),
    APPROVAL_PENDING: frozenset({READY, REJECTED}),
    READY: frozenset({DISPATCH_QUEUED}),
    DISPATCH_QUEUED: frozenset({SUBMITTING}),
    # The fork that defines the system. ACKNOWLEDGED and REJECTED are answers;
    # AMBIGUOUS is the absence of one.
    SUBMITTING: frozenset({ACKNOWLEDGED, AMBIGUOUS, REJECTED}),
    ACKNOWLEDGED: frozenset({PARTIALLY_FILLED, FILLED, CANCEL_PENDING}),
    # No self-edge. A second partial fill on an already-partial order is a new
    # Fill row, not a state change — the state says "some quantity is done",
    # the Fill ledger says how much.
    PARTIALLY_FILLED: frozenset({FILLED, CANCEL_PENDING}),
    # A cancel request can time out exactly as a submission can.
    CANCEL_PENDING: frozenset({CANCELED, AMBIGUOUS}),
    # Reconciliation resolves what the broker actually did; MANUAL_REVIEW is
    # the escape hatch when it cannot. Deliberately NO edge to SUBMITTING or
    # DISPATCH_QUEUED: see this module's docstring.
    AMBIGUOUS: frozenset({ACKNOWLEDGED, FILLED, CANCELED, MANUAL_REVIEW}),
    BLOCKED: frozenset(),
    REJECTED: frozenset(),
    FILLED: frozenset(),
    CANCELED: frozenset(),
    MANUAL_REVIEW: frozenset(),
}

STATES: Final[FrozenSet[str]] = frozenset(TRANSITIONS)


class IllegalTransition(ValueError):
    """Raised instead of writing a state the table does not permit."""

    def __init__(self, from_state: str, to_state: str, reason: str = "") -> None:
        self.from_state = from_state
        self.to_state = to_state
        detail = f": {reason}" if reason else ""
        super().__init__(f"{from_state} -> {to_state} is not a legal transition{detail}")


def is_terminal(state: str) -> bool:
    _require_known(state)
    return state in TERMINAL


def can_transition(from_state: str, to_state: str) -> bool:
    _require_known(from_state)
    _require_known(to_state)
    return to_state in TRANSITIONS[from_state]


def assert_transition(from_state: str, to_state: str) -> None:
    """Raise unless the move is legal. The guard every writer must go through."""
    if can_transition(from_state, to_state):
        return
    reason = ""
    if from_state in TERMINAL:
        reason = f"{from_state} is terminal"
    elif from_state == AMBIGUOUS and to_state in (SUBMITTING, DISPATCH_QUEUED):
        reason = (
            "an ambiguous submission must be resolved by reconciliation or a "
            "human, never resubmitted — the broker may already hold this order"
        )
    raise IllegalTransition(from_state, to_state, reason)


def _require_known(state: str) -> None:
    if state not in TRANSITIONS:
        raise IllegalTransition(state, state, f"{state!r} is not a known order state")
