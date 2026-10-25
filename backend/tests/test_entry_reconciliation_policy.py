from datetime import datetime, timedelta, timezone

import pytest

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



@pytest.mark.parametrize("field", ["verified", "clean", "scope_matches"])
@pytest.mark.parametrize("value", ["false", "true", 1, 0, [], {}, None])
def test_non_boolean_proof_flags_never_authorize(field, value):
    result = assess_new_entry_reconciliation(status(**{field: value}), now=NOW)
    assert not result.allowed
    assert result.reason == "reconciliation_evidence_invalid"


@pytest.mark.parametrize("evidence", [{}, object(), "verified"])
def test_wrong_evidence_object_never_authorizes(evidence):
    result = assess_new_entry_reconciliation(evidence, now=NOW)
    assert not result.allowed
    assert result.reason == "reconciliation_evidence_invalid"


@pytest.mark.parametrize("checked", ["2026-10-21T20:00:00Z", 123, object()])
def test_malformed_timestamp_denies_without_raising(checked):
    result = assess_new_entry_reconciliation(status(checked_at=checked), now=NOW)
    assert not result.allowed
    assert result.reason == "reconciliation_timestamp_unverified"
