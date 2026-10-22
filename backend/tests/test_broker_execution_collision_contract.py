"""Conflicting broker execution replays must fail without overwriting fills."""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.broker_fill_evidence import FillEvidence
from app.services.broker_execution_ingestion import record_authenticated_execution


def evidence():
    return FillEvidence(
        broker_account_id="paper-1", broker_execution_id="exec-1",
        contract_id="contract-1", underlying="SPY", expiration="2026-11-20",
        strike=Decimal("500"), option_type="put", signed_filled_quantity=-1,
        filled_at=datetime(2026, 10, 21, tzinfo=timezone.utc),
        order_id="order-1", organization_id=None,
    )


def fake_session(results):
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[
        SimpleNamespace(scalar_one_or_none=lambda value=value: value)
        for value in results
    ])
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=transaction)
    transaction.__aexit__ = AsyncMock(return_value=None)
    session.begin.return_value = transaction
    return session


async def persist(session):
    with patch("app.services.broker_execution_ingestion.AsyncSessionLocal", return_value=session):
        return await record_authenticated_execution(
            evidence(), broker="ibkr", expected_account_id="paper-1",
            expected_organization_id=None, broker_fill_price=Decimal("1.25")
        )


@pytest.mark.asyncio
async def test_first_execution_is_inserted():
    result = await persist(fake_session(["new-row-id"]))
    assert result.inserted is True


@pytest.mark.asyncio
async def test_conflicting_duplicate_rejected_without_update():
    original = evidence()
    existing = SimpleNamespace(
        organization_id=None, client_order_id=original.order_id,
        contract_id=original.contract_id, underlying=original.underlying,
        expiration=original.expiration, strike=original.strike,
        option_type=original.option_type, signed_filled_quantity=-2,
        fill_price=Decimal("1.25"), filled_at=original.filled_at,
    )
    session = fake_session([None, existing])
    with pytest.raises(ValueError, match="conflicting broker execution payload"):
        await persist(session)
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_identical_duplicate_is_idempotent():
    original = evidence()
    existing = SimpleNamespace(
        organization_id=None, client_order_id=original.order_id,
        contract_id=original.contract_id, underlying=original.underlying,
        expiration=original.expiration, strike=original.strike,
        option_type=original.option_type,
        signed_filled_quantity=original.signed_filled_quantity,
        fill_price=Decimal("1.25"), filled_at=original.filled_at,
    )
    result = await persist(fake_session([None, existing]))
    assert result.inserted is False
