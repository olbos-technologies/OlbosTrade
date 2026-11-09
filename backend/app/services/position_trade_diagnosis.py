"""Why a broker position does or does not have a trade row, in plain words.

Written for one symptom: three live positions (AMZN, FER, SPCX) showed the strategy
"UNKNOWN" on the Positions screen. That word means the screen found no OPEN trade
row it was allowed to show for that holding. This says which of several different
reasons applies, because they need different responses:

  tracked                    an open row exists and agrees with the broker
  quantity_mismatch /
  direction_mismatch         a row exists but disagrees on size or side
  hidden_from_positions_page an open row exists but is owned by someone else, so the
                             strict Positions screen shows "unknown" even though the
                             desk itself (reconciler, duplicate guard) can see it
  pending_confirmation       the row is still waiting for the fill to be confirmed
  only_closed_rows           the rows say closed, the broker still holds it
  no_trade_row               nothing was ever recorded for it
  row_without_broker_position  an open row, nothing at the broker

Pure functions: no database, no broker, no clock, so every case is testable.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

TRACKED = "tracked"
QUANTITY_MISMATCH = "quantity_mismatch"
DIRECTION_MISMATCH = "direction_mismatch"
HIDDEN = "hidden_from_positions_page"
OTHER_OWNER = "open_row_other_owner"
PENDING = "pending_confirmation"
ONLY_CLOSED = "only_closed_rows"
NO_ROW = "no_trade_row"
ROW_NO_BROKER = "row_without_broker_position"

#: Which findings deserve a look. `info` is explained and harmless to the book.
SEVERITY = {
    TRACKED: "ok", PENDING: "info", HIDDEN: "attention", OTHER_OWNER: "attention",
    QUANTITY_MISMATCH: "attention", DIRECTION_MISMATCH: "attention",
    ONLY_CLOSED: "attention", NO_ROW: "attention", ROW_NO_BROKER: "attention",
}

OPEN_STATUSES = frozenset({"open"})
PENDING_STATUSES = frozenset({"pending"})


@dataclass(frozen=True)
class TradeRow:
    id: str
    status: str
    strategy: str
    asset_class: str                 # "equity" | "options"
    direction: int                   # +1 long, -1 short, 0 unknown / not applicable
    quantity: Optional[int]
    organization_id: Optional[uuid.UUID]
    entry_date: Optional[datetime]
    exit_date: Optional[datetime]
    approved_by: Optional[str]


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    asset_class: str
    quantity: int                    # signed for equity


@dataclass(frozen=True)
class Diagnosis:
    code: str
    explanation: str

    @property
    def severity(self) -> str:
        return SEVERITY[self.code]


def owner_label(org: Optional[uuid.UUID], viewer: Optional[uuid.UUID],
                operator: Optional[uuid.UUID]) -> str:
    """Whose row it is, from the viewer's side."""
    if org == viewer:
        return "yours" if org is not None else "unattributed (made before ownership existed)"
    if org is None:
        return "unattributed (made before ownership existed)"
    if operator is not None and org == operator:
        return "the operator organization"
    return "another organization"


def visible_to_positions_page(row: TradeRow, viewer: Optional[uuid.UUID]) -> bool:
    """What `GET /api/paper-trade/positions` can show: STRICT ownership only.

    `None` is a scope (the unattributed rows), not a wildcard, so None matches
    only None. The desk's own safety checks widen this for the designated
    operator; a screen deliberately does not.
    """
    return row.organization_id == viewer


def visible_to_the_desk(row: TradeRow, viewer: Optional[uuid.UUID],
                        operator: Optional[uuid.UUID]) -> bool:
    """What the reconciler and duplicate guard can see: strict, widened for the
    designated operator so they also own the unattributed rows."""
    if row.organization_id == viewer:
        return True
    return operator is not None and viewer == operator and row.organization_id is None


def _sign(n: int) -> int:
    return 1 if n > 0 else -1 if n < 0 else 0


def diagnose(
    broker: Optional[BrokerPosition],
    rows: list[TradeRow],
    viewer: Optional[uuid.UUID],
    operator: Optional[uuid.UUID],
) -> Diagnosis:
    """One holding (or one orphaned row group) -> its diagnosis."""
    same_class = [r for r in rows if broker is None or r.asset_class == broker.asset_class]
    open_rows = [r for r in same_class if r.status in OPEN_STATUSES]

    if broker is None:
        if open_rows:
            return Diagnosis(ROW_NO_BROKER, (
                "The database has an open trade but the broker shows no position for it. "
                "The reconciler closes these after a grace period once it has the exit price."
            ))
        raise ValueError("nothing to diagnose: no broker position and no open row")

    visible = [r for r in open_rows if visible_to_positions_page(r, viewer)]
    if visible:
        mismatch = _compare(broker, visible)
        if mismatch is not None:
            return mismatch
        return Diagnosis(TRACKED, "An open trade row exists and agrees with the broker.")

    if open_rows:
        owners = sorted({owner_label(r.organization_id, viewer, operator) for r in open_rows})
        by_desk = [r for r in open_rows if visible_to_the_desk(r, viewer, operator)]
        who = " and ".join(owners)
        if by_desk:
            return Diagnosis(HIDDEN, (
                f"An open trade row exists, but it is {who}, not owned by the account "
                "you are signed in as. The Positions screen shows only your own rows, so "
                "it says UNKNOWN for this holding. The desk itself (the reconciler and the "
                "duplicate-order guard) does count this row. Nothing is wrong with the "
                "position; the label is a display scope."
            ))
        return Diagnosis(OTHER_OWNER, (
            f"An open trade row exists but it is {who}. Neither this screen nor the desk's "
            "own checks for your account can see it."
        ))

    if any(r.status in PENDING_STATUSES for r in same_class):
        return Diagnosis(PENDING, (
            "A pending row exists for this name: the entry was sent and the fill is not "
            "confirmed yet. The fill poller promotes it to open, or cancels it after its "
            "grace period."
        ))

    closed = [r for r in same_class]
    if closed:
        last = max((r.exit_date for r in closed if r.exit_date), default=None)
        when = f" (last closed {last.date().isoformat()})" if last else ""
        return Diagnosis(ONLY_CLOSED, (
            "Every trade row for this name says closed or cancelled" + when + ", yet the "
            "broker still holds it. Either the exit was booked but the position was not "
            "actually flat, or it was re-entered outside the app. The reconciler will "
            "adopt it as an inherited position."
        ))

    return Diagnosis(NO_ROW, (
        "No trade row was ever recorded for this name, so the app has no record of "
        "opening it. If it was placed by this app, the entry's bookkeeping was lost "
        "(see the ENTRY_UNRECORDED log marker); otherwise it came from outside the app."
    ))


def _compare(broker: BrokerPosition, visible: list[TradeRow]) -> Optional[Diagnosis]:
    """Size and side, for equity only: an option spread's legs have no signed total."""
    if broker.asset_class != "equity":
        return None
    sides = {r.direction for r in visible if r.direction}
    if sides and _sign(broker.quantity) not in sides:
        return Diagnosis(DIRECTION_MISMATCH, (
            "The open row and the broker disagree on which SIDE this position is on "
            "(long versus short). The reconciler reports this and never auto-corrects it."
        ))
    if all(r.direction for r in visible) and all(r.quantity is not None for r in visible):
        recorded = sum(r.direction * int(r.quantity) for r in visible)
        if recorded != broker.quantity:
            return Diagnosis(QUANTITY_MISMATCH, (
                f"The open rows add up to {recorded:+d} shares but the broker holds "
                f"{broker.quantity:+d}."
            ))
    return None
