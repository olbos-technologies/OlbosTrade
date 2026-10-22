"""R2-N: fail-closed preflight policy for exposure-changing orders.

Pure decision logic. Does not connect to a broker or submit orders.
Do not classify an unknown or mixed-intent order as an authorized exit.
"""
from dataclasses import dataclass
from datetime import datetime

from app.services.entry_reconciliation_policy import (
    EntryReconciliationStatus, assess_new_entry_reconciliation,
)


@dataclass(frozen=True)
class OrderPreflightDecision:
    allowed: bool
    reason: str


def assess_order_preflight(
    *, intent: str, reconciliation: EntryReconciliationStatus | None,
    now: datetime, authorized_close: bool = False,
) -> OrderPreflightDecision:
    """Deny unproven entries; require separate authorization for closes.

    A close flag alone is not sufficient to prove the order reduces exposure.
    Integration must separately validate account ownership, leg quantities,
    position availability, and close-only order capabilities.
    """
    if intent == "close":
        return OrderPreflightDecision(False, "close_requires_position_bound_authorization")
    if intent != "open":
        return OrderPreflightDecision(False, "unknown_or_mixed_order_intent")
    result = assess_new_entry_reconciliation(reconciliation, now=now)
    return OrderPreflightDecision(result.allowed, result.reason or "reconciliation_verified")
