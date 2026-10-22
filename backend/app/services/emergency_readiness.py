"""R2-O emergency-stop readiness assessment (read-only, fail-closed).

Observes a kill-switch result; never engages/disengages it or sends orders.
A successful cancellation/flatten submission is NOT proof of flat holdings.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class EmergencyReadiness:
    safe_to_resume: bool
    reasons: tuple[str, ...]


def assess_emergency_stop(status: dict | None, result: dict | None) -> EmergencyReadiness:
    reasons = []
    if not isinstance(status, dict) or status.get("engaged") is not True:
        reasons.append("kill_switch_not_confirmed_engaged")
    if not isinstance(status, dict) or status.get("rehydrate_unverified") is not False:
        reasons.append("kill_switch_state_unverified")
    if not isinstance(result, dict):
        reasons.append("emergency_result_unavailable")
    else:
        if result.get("errors"):
            reasons.append("emergency_actions_report_errors")
        if result.get("scheduler_paused") is not True:
            reasons.append("scheduler_pause_unverified")
        if result.get("cancel_attempted") is not True:
            reasons.append("order_cancellation_unverified")
        if result.get("unresolved_orders") or not isinstance(result.get("unresolved_orders"), dict):
            reasons.append("working_orders_unresolved_or_unknown")
        rec = result.get("reconciliation")
        if not isinstance(rec, dict) or rec.get("performed") is not True or rec.get("flat") is not True:
            reasons.append("broker_flatness_unverified")
        if result.get("db_persisted") is not True:
            reasons.append("emergency_audit_persistence_unverified")
    # Deliberately no automatic resume authorization; separate operator
    # approval, account-scoped reconciliation and durable release are required.
    reasons.append("manual_resume_authorization_required")
    return EmergencyReadiness(False, tuple(reasons))
