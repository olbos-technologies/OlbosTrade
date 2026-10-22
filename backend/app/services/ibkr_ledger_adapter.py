"""R2-H isolated IBKR fill-to-ledger adapter.

Caller must supply authenticated, account-owned IBKR Fill objects. This module
does not register callbacks, query brokers, open positions or place orders.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.services.ibkr_execution_normalization import normalize_ibkr_option_fill
from app.services.broker_execution_ingestion import record_authenticated_execution


@dataclass(frozen=True)
class IngestionBatchResult:
    inserted: int
    repeated: int


async def ingest_ibkr_option_fills(
    fills, *, expected_account_id: str, organization_id: uuid.UUID | None,
) -> IngestionBatchResult:
    """Validate entire batch before writing any individual execution.

    Broker-supplied records are not authenticated by this method: the calling
    broker session must prove account ownership and history completeness.
    """
    org = str(organization_id) if organization_id is not None else None
    normalized = [
        normalize_ibkr_option_fill(
            fill, expected_account=expected_account_id, organization_id=org
        ) for fill in fills
    ]
    inserted = repeated = 0
    for entry in normalized:
        result = await record_authenticated_execution(
            entry.evidence, broker="ibkr",
            expected_account_id=expected_account_id,
            expected_organization_id=organization_id,
            broker_fill_price=entry.price,
        )
        if result.inserted:
            inserted += 1
        else:
            repeated += 1
    return IngestionBatchResult(inserted, repeated)
