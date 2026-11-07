"""Fresh, read-only reconciliation before operator-account IBKR entry sends.

This is an entry veto, not a kill switch or an automatic position repair.
IBKR is the single-operator account in this process (worker_scope.py); never
compare its holdings against an organization's separate broker or trades.
"""
import asyncio

from app.services.execution_interlock import EntryHalted, check_entry_permit


class ReconciliationBlocked(EntryHalted):
    """No entry sent because account reconciliation is unresolved/unavailable."""


async def require_reconciled_entry(broker, permit):
    if permit is None:  # reviewed risk-reducing paths, including emergency exits
        return
    check_entry_permit(permit)
    from app.services.position_reconciler import PositionReconciler
    try:
        result = await asyncio.wait_for(
            PositionReconciler(broker, organization_id=None).check(fresh_broker=True),
            timeout=5.0,
        )
    except Exception as exc:
        raise ReconciliationBlocked("reconciliation_unavailable") from exc
    if result.clean is not True:
        raise ReconciliationBlocked("reconciliation_unresolved")
    # A halt during the DB/broker await must still invalidate this send.
    check_entry_permit(permit)
