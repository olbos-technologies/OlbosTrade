from datetime import datetime, timedelta, timezone
from app.services.entry_reconciliation_policy import EntryReconciliationStatus
from app.services.entry_submission_boundary import check_entry_before_submission

NOW=datetime(2026,10,22,tzinfo=timezone.utc)
OK=EntryReconciliationStatus(True,True,NOW,True)

def evaluate(**changes):
    values=dict(intent="open",reconciliation=OK,now=NOW,
                kill_switch_engaged=False,account_scope_authenticated=True)
    values.update(changes)
    return check_entry_before_submission(**values)

def test_ready_entry_only_passes_this_preflight():
    assert evaluate().allowed is True

def test_missing_or_engaged_kill_switch_blocks():
    for value in (True,None):
        assert not evaluate(kill_switch_engaged=value).allowed

def test_unverified_account_blocks():
    assert not evaluate(account_scope_authenticated=False).allowed

def test_no_execution_history_proof_blocks():
    assert not evaluate(reconciliation=None).allowed
    assert not evaluate(reconciliation=EntryReconciliationStatus(False,False,NOW,True)).allowed

def test_stale_reconciliation_and_closes_block():
    stale=EntryReconciliationStatus(True,True,NOW-timedelta(minutes=2),True)
    assert not evaluate(reconciliation=stale).allowed
    assert not evaluate(intent="close").allowed
    assert not evaluate(intent="mixed").allowed
