"""R2-G read-only IBKR execution normalization (not connected to callbacks).

Only the authenticated broker adapter may supply execution objects and the
expected account/organization scope. Never use order status as fill evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.services.broker_fill_evidence import FillEvidence, validate_fill_evidence


@dataclass(frozen=True)
class NormalizedExecution:
    evidence: FillEvidence
    price: Decimal


def normalize_ibkr_option_fill(fill, *, expected_account: str,
                               organization_id: str | None) -> NormalizedExecution:
    """Reject malformed/non-option or cross-account IBKR execution reports."""
    try:
        execution = fill.execution
        contract = fill.contract
        if str(contract.secType).upper() != "OPT":
            raise ValueError("non_option_contract")
        account = str(execution.acctNumber).strip()
        if not account or account != expected_account:
            raise ValueError("account_scope_mismatch")
        side = str(execution.side).upper()
        if side not in ("BOT", "SLD"):
            raise ValueError("invalid_execution_side")
        shares = Decimal(str(execution.shares))
        if not shares.is_finite() or shares <= 0 or shares != shares.to_integral_value():
            raise ValueError("invalid_filled_quantity")
        right = str(contract.right).upper()
        if right not in ("P", "C"):
            raise ValueError("invalid_option_right")
        expiry = str(contract.lastTradeDateOrContractMonth)
        if len(expiry) != 8 or not expiry.isdigit():
            raise ValueError("invalid_expiration")
        from datetime import date
        expiry_date = date(int(expiry[:4]), int(expiry[4:6]), int(expiry[6:]))
        strike = Decimal(str(contract.strike))
        price = Decimal(str(execution.price))
        if not strike.is_finite() or strike <= 0 or not price.is_finite() or price <= 0:
            raise ValueError("invalid_price_or_strike")
        timestamp = execution.time
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None:
            raise ValueError("execution_timestamp_unverified")
        evidence = FillEvidence(
            broker_account_id=account, broker_execution_id=str(execution.execId),
            contract_id=str(contract.conId), underlying=str(contract.symbol),
            expiration=expiry_date.isoformat(), strike=strike,
            option_type="put" if right == "P" else "call",
            signed_filled_quantity=int(shares) * (1 if side == "BOT" else -1),
            filled_at=timestamp, order_id=str(execution.orderId),
            organization_id=organization_id,
        )
        checked = validate_fill_evidence(
            evidence, expected_account_id=expected_account,
            expected_organization_id=organization_id,
        )
        if not checked.verified or not evidence.contract_id.isdigit() or int(evidence.contract_id) <= 0:
            raise ValueError("invalid_execution_evidence: " + ",".join(checked.reasons))
        return NormalizedExecution(evidence, price)
    except (AttributeError, TypeError, InvalidOperation, OverflowError) as exc:
        raise ValueError("malformed_ibkr_fill") from exc
