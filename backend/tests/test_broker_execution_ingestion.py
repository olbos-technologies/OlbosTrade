"""Ingestion boundary checks: rejects unpriced or cross-account executions."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.services.broker_fill_evidence import FillEvidence
from app.services.broker_execution_ingestion import record_authenticated_execution


def evidence(**changes):
    data = dict(
        broker_account_id="paper-1", broker_execution_id="e1",
        contract_id="c1", underlying="SPY", expiration="2026-11-20",
        strike=Decimal("500"), option_type="put", signed_filled_quantity=-1,
        filled_at=datetime.now(timezone.utc), order_id="o1",
        organization_id=None,
    )
    data.update(changes)
    return FillEvidence(**data)


@pytest.mark.asyncio
async def test_rejects_missing_price_before_database_write():
    with patch("app.services.broker_execution_ingestion.AsyncSessionLocal") as session:
        with pytest.raises(ValueError, match="fill price"):
            await record_authenticated_execution(
                evidence(), broker="ibkr", expected_account_id="paper-1",
                expected_organization_id=None, broker_fill_price=Decimal("0"),
            )
        session.assert_not_called()


@pytest.mark.asyncio
async def test_rejects_account_mismatch_before_database_write():
    with patch("app.services.broker_execution_ingestion.AsyncSessionLocal") as session:
        with pytest.raises(ValueError, match="account_scope_mismatch"):
            await record_authenticated_execution(
                evidence(), broker="ibkr", expected_account_id="other",
                expected_organization_id=None, broker_fill_price=Decimal("1.25"),
            )
        session.assert_not_called()
