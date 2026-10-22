from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from app.services.ibkr_ledger_adapter import ingest_ibkr_option_fills
from app.services.broker_execution_ingestion import FillWriteResult


def fill(account="DU123", execution_id="exec-1"):
    return SimpleNamespace(
        time=datetime(2026,10,22,tzinfo=timezone.utc),
        execution=SimpleNamespace(acctNumber=account,side="BOT",shares=1,price=1.20,
                                  execId=execution_id,orderId=123),
        contract=SimpleNamespace(secType="OPT",right="C",symbol="SPY",
                                 lastTradeDateOrContractMonth="20261120",
                                 strike=510,conId=123456),
    )


@pytest.mark.asyncio
async def test_new_and_repeated_fill_results():
    writer=AsyncMock(side_effect=[
        FillWriteResult(True,"exec-1"),FillWriteResult(False,"exec-1")
    ])
    with patch("app.services.ibkr_ledger_adapter.record_authenticated_execution",writer):
        result=await ingest_ibkr_option_fills([fill(),fill()],expected_account_id="DU123",organization_id=None)
    assert result.inserted==1 and result.repeated==1
    assert writer.await_count==2


@pytest.mark.asyncio
async def test_cross_account_batch_is_rejected_before_any_write():
    writer=AsyncMock()
    with patch("app.services.ibkr_ledger_adapter.record_authenticated_execution",writer):
        with pytest.raises(ValueError,match="account_scope_mismatch"):
            await ingest_ibkr_option_fills([fill(),fill("DU999","exec-2")],
                                            expected_account_id="DU123",organization_id=None)
    writer.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_batch_does_not_claim_history_completeness():
    writer=AsyncMock()
    with patch("app.services.ibkr_ledger_adapter.record_authenticated_execution",writer):
        result=await ingest_ibkr_option_fills([],expected_account_id="DU123",organization_id=None)
    assert result.inserted==0 and result.repeated==0
    writer.assert_not_awaited()
