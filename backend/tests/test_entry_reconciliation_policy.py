from datetime import datetime, timedelta, timezone

from app.services.entry_reconciliation_policy import (
    EntryReconciliationStatus, assess_new_entry_reconciliation,
)


NOW = datetime(2026, 10, 21, 20, 0, tzinfo=timezone.utc)


def status(**changes):
    data = dict(verified=True, clean=True, checked_at=NOW,
                scope_matches=True, reason=None)
    data.update(changes)
    return EntryReconciliationStatus(**data)


def test_recent_verified_matching_scope_allows_entry():
    assert assess_new_entry_reconciliation(status(), now=NOW).allowed


def test_missing_or_unverified_state_blocks_entry():
    assert not assess_new_entry_reconciliation(None, now=NOW).allowed
    assert not assess_new_entry_reconciliation(status(verified=False), now=NOW).allowed


def test_reconciliation_mismatch_blocks_entry():
    decision = assess_new_entry_reconciliation(status(clean=False), now=NOW)
    assert not decision.allowed
    assert decision.reason == "reconciliation_mismatch"


def test_wrong_tenant_scope_blocks_entry():
    assert not assess_new_entry_reconciliation(status(scope_matches=False), now=NOW).allowed


def test_stale_future_and_missing_timestamps_block_entry():
    for checked in (NOW - timedelta(seconds=61), NOW + timedelta(seconds=1), None):
        assert not assess_new_entry_reconciliation(status(checked_at=checked), now=NOW).allowed


def test_naive_timestamp_does_not_authorize_entry():
    assert not assess_new_entry_reconciliation(
        status(checked_at=datetime(2026, 10, 21, 20)), now=NOW
    ).allowed
