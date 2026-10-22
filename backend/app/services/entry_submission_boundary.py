"""R2-P: fail-closed pre-submission boundary for new orders.

No broker integration is enabled by this module. Caller must supply a fresh,
independently authenticated and complete reconciliation proof. Passing only
means this specific preflight allows proceeding to *other* risk checks.
"""
from datetime import datetime

from app.services.entry_reconciliation_policy import EntryReconciliationStatus
from app.services.order_reconciliation_preflight import (
    OrderPreflightDecision, assess_order_preflight,
)


def check_entry_before_submission(
    *, intent: str, reconciliation: EntryReconciliationStatus | None,
    now: datetime, kill_switch_engaged: bool | None,
    account_scope_authenticated: bool,
) -> OrderPreflightDecision:
    """Never interpret missing kill-switch/account state as trading permission."""
    if kill_switch_engaged is not False:
        return OrderPreflightDecision(False, "kill_switch_state_unverified_or_engaged")
    if account_scope_authenticated is not True:
        return OrderPreflightDecision(False, "broker_account_authentication_unverified")
    return assess_order_preflight(intent=intent, reconciliation=reconciliation, now=now)
