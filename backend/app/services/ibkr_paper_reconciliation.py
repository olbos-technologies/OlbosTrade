"""R2-M: conservative read-only IBKR paper-position comparison.

A local IBKR fills() cache cannot establish a complete execution history.
This helper NEVER grants history coverage, and cannot authorize new entries.
"""
from __future__ import annotations

from app.services.contract_reconciliation import ContractComparison
from app.services.ledger_reconciliation import compare_scoped_broker_to_ledger


def compare_ibkr_paper_snapshot(
    session, broker_legs, ledger_rows, *,
    expected_account_id: str, organization_id,
) -> ContractComparison:
    """Compare holdings for diagnostics, without claiming history is complete."""
    account = expected_account_id.strip()
    if not account.startswith("DU") or not account[2:].isdigit():
        return ContractComparison(False, False, reasons=["ibkr_paper_account_required"])
    if not session.isConnected():
        return ContractComparison(False, False, reasons=["broker_session_disconnected"])
    accounts = tuple(session.managedAccounts())
    if accounts != (account,):
        return ContractComparison(False, False, reasons=["broker_session_account_mismatch"])
    if broker_legs is None or ledger_rows is None:
        return ContractComparison(False, False, reasons=["position_or_ledger_unavailable"])
    # Explicitly refuse to infer coverage from the session's fill cache,
    # even if all currently observed contract positions happen to match.
    return compare_scoped_broker_to_ledger(
        broker_legs, ledger_rows, broker="ibkr",
        broker_account_id=account, organization_id=organization_id,
        coverage_verified=False,
    )
