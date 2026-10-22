from datetime import datetime,timedelta,timezone
from app.services.entry_reconciliation_policy import EntryReconciliationStatus
from app.services.order_reconciliation_preflight import assess_order_preflight

NOW=datetime(2026,10,22,tzinfo=timezone.utc)

def status(**overrides):
    values=dict(verified=True,clean=True,checked_at=NOW,
        scope_matches=True,reason=None)
    values.update(overrides)
    return EntryReconciliationStatus(**values)

def test_clean_fresh_entry_is_eligible_for_other_risk_checks():
    result=assess_order_preflight(intent="open",reconciliation=status(),now=NOW)
    assert result.allowed

def test_stale_incomplete_mismatched_and_unscoped_entries_denied():
    for current in (None,status(verified=False),status(clean=False),
                    status(scope_matches=False),
                    status(checked_at=NOW-timedelta(minutes=2))):
        result=assess_order_preflight(intent="open",reconciliation=current,now=NOW)
        assert not result.allowed

def test_unknown_mixed_and_close_intents_fail_closed():
    for intent in ("unknown","mixed","close"):
        result=assess_order_preflight(intent=intent,reconciliation=status(),
            now=NOW,authorized_close=True)
        assert not result.allowed
