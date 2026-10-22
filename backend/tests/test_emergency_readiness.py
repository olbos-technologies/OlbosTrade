from app.services.emergency_readiness import assess_emergency_stop


def test_missing_emergency_evidence_never_authorizes_resume():
    result=assess_emergency_stop(None,None)
    assert result.safe_to_resume is False
    assert "broker_flatness_unverified" not in result.reasons
    assert "manual_resume_authorization_required" in result.reasons


def test_all_green_report_still_requires_explicit_manual_resume():
    status={"engaged":True,"rehydrate_unverified":False}
    report={"errors":[],"scheduler_paused":True,"cancel_attempted":True,
        "unresolved_orders":{},"reconciliation":{"performed":True,"flat":True},
        "db_persisted":True}
    result=assess_emergency_stop(status,report)
    assert result.safe_to_resume is False
    assert result.reasons==("manual_resume_authorization_required",)


def test_accepted_flatten_without_broker_confirmation_fails_closed():
    status={"engaged":True,"rehydrate_unverified":False}
    report={"errors":[],"scheduler_paused":True,"cancel_attempted":True,
        "unresolved_orders":{},"reconciliation":{"performed":False,"flat":None},
        "db_persisted":True}
    result=assess_emergency_stop(status,report)
    assert result.safe_to_resume is False
    assert "broker_flatness_unverified" in result.reasons


def test_unresolved_orders_or_failed_audit_are_reported():
    status={"engaged":True,"rehydrate_unverified":False}
    report={"errors":["cancel_failed"],"scheduler_paused":False,
        "cancel_attempted":False,"unresolved_orders":{"order-1":"working"},
        "reconciliation":{"performed":True,"flat":False},"db_persisted":False}
    reasons=assess_emergency_stop(status,report).reasons
    assert "working_orders_unresolved_or_unknown" in reasons
    assert "emergency_audit_persistence_unverified" in reasons
