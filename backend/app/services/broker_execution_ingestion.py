"""Write broker-authenticated execution evidence once per broker/account/execution ID.

This service is not wired to brokerage callbacks or order submission. Caller
must authenticate account ownership and broker source before invoking it.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert

from app.core.database import AsyncSessionLocal
from app.models.broker_execution_fill import BrokerExecutionFill
from app.services.broker_fill_evidence import FillEvidence, validate_fill_evidence


@dataclass(frozen=True)
class FillWriteResult:
    inserted: bool
    broker_execution_id: str


async def record_authenticated_execution(
    evidence: FillEvidence,
    *,
    broker: str,
    expected_account_id: str,
    expected_organization_id: uuid.UUID | None,
) -> FillWriteResult:
    """Idempotent atomic insert; identity collisions never update old fills.

    A conflicting execution ID with different payload remains untouched and
    requires explicit investigation. No inferred positions or fill quantities.
    """
    expected_org = str(expected_organization_id) if expected_organization_id else None
    validation = validate_fill_evidence(
        evidence, expected_account_id=expected_account_id,
        expected_organization_id=expected_org,
    )
    if not validation.verified:
        raise ValueError("invalid fill evidence: " + ", ".join(validation.reasons))
    if not broker.strip():
        raise ValueError("broker identity missing")
    if not evidence.expiration or len(evidence.expiration) != 10:
        raise ValueError("invalid expiration")
    values = dict(
        id=uuid.uuid4(), broker=broker.strip().lower(),
        broker_account_id=evidence.broker_account_id,
        broker_execution_id=evidence.broker_execution_id,
        organization_id=expected_organization_id,
        client_order_id=evidence.order_id,
        contract_id=evidence.contract_id,
        underlying=evidence.underlying,
        expiration=evidence.expiration,
        strike=Decimal(str(evidence.strike)),
        option_type=evidence.option_type,
        signed_filled_quantity=evidence.signed_filled_quantity,
        fill_price=Decimal("0"),  # price not included in FillEvidence: NEVER invent it
        filled_at=evidence.filled_at,
    )
    # Reject rather than store a fabricated fill price. A priced broker
    # execution schema is required before persistence can be enabled.
    raise ValueError("broker execution fill price unavailable; ingestion disabled")
