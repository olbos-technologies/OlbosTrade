"""R2-Q temporary fail-closed submission gate regression.

No complete-history provider exists yet, therefore the entry pipeline must
not reach broker order placement. This test only checks the present boundary;
execution integration still needs full authenticated evidence coverage.
"""
from datetime import datetime, timezone
from app.services.entry_submission_boundary import check_entry_before_submission


def test_missing_authenticated_reconciliation_denies_new_entry():
    decision = check_entry_before_submission(
        intent="open", reconciliation=None, now=datetime.now(timezone.utc),
        kill_switch_engaged=False, account_scope_authenticated=False,
    )
    assert not decision.allowed


def test_common_execution_pipeline_uses_submission_gate_before_broker():
    import inspect
    from app.api.routes.trade_desk import _execute_signal
    source = inspect.getsource(_execute_signal)
    assert "check_entry_before_submission(" in source
    assert source.index("check_entry_before_submission(") < source.index(
        "broker.place_equity_order("
    )
    assert source.index("check_entry_before_submission(") < source.index(
        "broker.place_order(order)"
    )


def test_no_broker_authority_from_reconciliation_claim_alone():
    from app.services.entry_reconciliation_policy import EntryReconciliationStatus
    now = datetime.now(timezone.utc)
    purported_proof = EntryReconciliationStatus(
        verified=True, clean=True, checked_at=now, scope_matches=True,
    )
    decision = check_entry_before_submission(
        intent="open", reconciliation=purported_proof, now=now,
        kill_switch_engaged=False, account_scope_authenticated=False,
    )
    assert not decision.allowed
    assert decision.reason == "broker_account_authentication_unverified"


def test_entry_denied_even_with_account_claim_when_history_missing():
    decision = check_entry_before_submission(
        intent="open", reconciliation=None, now=datetime.now(timezone.utc),
        kill_switch_engaged=False, account_scope_authenticated=True,
    )
    assert not decision.allowed
    assert decision.reason == "reconciliation_unavailable"
