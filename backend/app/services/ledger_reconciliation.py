"""R2-F read-only signed option leg aggregation from durable fills.

Only call when the ingestion source is authenticated and its historical
coverage is independently established. This function does not attest that the
ledger is complete, nor permit trading by itself.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from app.services.contract_reconciliation import ContractLeg, ContractComparison, compare_contract_legs


def legs_from_executions(rows) -> list[ContractLeg] | None:
    """Return None on any malformed execution rather than silently omit it."""
    totals = defaultdict(int)
    try:
        for row in rows:
            qty = row.signed_filled_quantity
            if type(qty) is not int or qty == 0:
                return None
            expiry = date.fromisoformat(row.expiration)
            strike = Decimal(str(row.strike))
            kind = str(row.option_type).strip().lower()
            name = str(row.underlying).strip().upper()
            if not name or kind not in {"put", "call"} or not strike.is_finite() or strike <= 0:
                return None
            totals[(name, expiry, strike, kind)] += qty
    except (AttributeError, TypeError, ValueError, InvalidOperation):
        return None
    return [ContractLeg(symbol, expiry, strike, kind, qty)
            for (symbol, expiry, strike, kind), qty in totals.items() if qty]


def compare_broker_to_ledger(broker_legs, ledger_rows, *, coverage_verified: bool) -> ContractComparison:
    """Without explicit ledger history coverage, never report a clean book."""
    if not coverage_verified or ledger_rows is None or broker_legs is None:
        return ContractComparison(False, False, reasons=["execution_coverage_unverified"])
    recorded = legs_from_executions(ledger_rows)
    if recorded is None:
        return ContractComparison(False, False, reasons=["invalid_execution_ledger"])
    return compare_contract_legs(broker_legs, recorded)
