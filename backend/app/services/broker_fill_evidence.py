"""R2-D: proof requirements for an executed broker fill.

An accepted order, a pending trade row, or a strategy-intent spread is not
sufficient evidence that its option legs were actually filled. This pure
validator is not yet wired to execution or persistence.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class FillEvidence:
    broker_account_id: str
    broker_execution_id: str
    contract_id: str
    underlying: str
    expiration: str
    strike: Decimal
    option_type: str
    signed_filled_quantity: int
    filled_at: datetime
    order_id: str
    organization_id: str | None


@dataclass(frozen=True)
class FillValidation:
    verified: bool
    reasons: tuple[str, ...] = ()


def validate_fill_evidence(evidence: FillEvidence | None, *,
                           expected_account_id: str,
                           expected_organization_id: str | None) -> FillValidation:
    if evidence is None:
        return FillValidation(False, ("fill_evidence_missing",))
    failures = []
    for field in ("broker_account_id", "broker_execution_id", "contract_id",
                  "underlying", "expiration", "option_type", "order_id"):
        if not str(getattr(evidence, field, "") or "").strip():
            failures.append(f"{field}_missing")
    if evidence.broker_account_id != expected_account_id or (
        evidence.organization_id != expected_organization_id
    ):
        failures.append("account_scope_mismatch")
    if evidence.option_type not in ("call", "put"):
        failures.append("unsupported_option_type")
    if not isinstance(evidence.signed_filled_quantity, int) or evidence.signed_filled_quantity == 0:
        failures.append("filled_quantity_invalid")
    if not isinstance(evidence.filled_at, datetime) or evidence.filled_at.tzinfo is None:
        failures.append("fill_timestamp_unverified")
    try:
        if not Decimal(str(evidence.strike)).is_finite() or evidence.strike <= 0:
            failures.append("strike_invalid")
    except (TypeError, ValueError, ArithmeticError):
        failures.append("strike_invalid")
    return FillValidation(not failures, tuple(failures))
