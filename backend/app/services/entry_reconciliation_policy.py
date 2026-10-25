"""R2-B reconciliation decision policy for *new* trading entries.

Pure, conservative policy only. No broker calls or execution path wiring.
Reconciliation proof must be scoped, fresh, contract-complete and clean.
Authorized exits are outside this entry-specific decision.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class EntryReconciliationStatus:
    verified: bool
    clean: bool
    checked_at: datetime | None
    scope_matches: bool
    reason: str | None = None


@dataclass(frozen=True)
class EntryReconciliationDecision:
    allowed: bool
    reason: str | None = None


def assess_new_entry_reconciliation(
    status: EntryReconciliationStatus | None,
    *,
    now: datetime,
    maximum_age: timedelta = timedelta(seconds=60),
) -> EntryReconciliationDecision:
    """An unknown, unsafely scoped or stale check is *not* entry authorization.

    Integration must fail closed on reconciliation service exceptions and
    must avoid using a monitoring-only symbol-count check as proof.
    """
    if status is None:
        return EntryReconciliationDecision(False, "reconciliation_unavailable")
    if not isinstance(status, EntryReconciliationStatus) or any(
        type(value) is not bool
        for value in (status.verified, status.clean, status.scope_matches)
    ):
        return EntryReconciliationDecision(False, "reconciliation_evidence_invalid")
    if not status.scope_matches:
        return EntryReconciliationDecision(False, "reconciliation_scope_unverified")
    if not status.verified:
        return EntryReconciliationDecision(False, status.reason or "reconciliation_unverified")
    if not status.clean:
        return EntryReconciliationDecision(False, status.reason or "reconciliation_mismatch")
    checked_at = status.checked_at
    if (not isinstance(checked_at, datetime) or not isinstance(now, datetime)
            or checked_at.tzinfo is None or now.tzinfo is None):
        return EntryReconciliationDecision(False, "reconciliation_timestamp_unverified")
    age = now.astimezone(timezone.utc) - checked_at.astimezone(timezone.utc)
    if age < timedelta(0) or age > maximum_age:
        return EntryReconciliationDecision(False, "reconciliation_stale")
    return EntryReconciliationDecision(True)
