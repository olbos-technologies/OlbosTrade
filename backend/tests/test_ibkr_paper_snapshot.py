from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import pytest
from app.services.ibkr_paper_snapshot import ingest_paper_execution_snapshot


def session(accounts=("DU123",), connected=True):
    return SimpleNamespace(
        isConnected=lambda: connected,
        managedAccounts=lambda: list(accounts),
        fills=lambda: [],
    )


@pytest.mark.asyncio
async def test_paper_account_snapshot_uses_scoped_adapter():
    adapter=AsyncMock()
    with patch("app.services.ibkr_paper_snapshot.ingest_ibkr_option_fills",adapter):
        await ingest_paper_execution_snapshot(session(),expected_account_id="DU123",organization_id=None)
    adapter.assert_awaited_once_with((),expected_account_id="DU123",organization_id=None)


@pytest.mark.asyncio
async def test_live_or_unrecognized_account_rejected_before_ingestion():
    adapter=AsyncMock()
    with patch("app.services.ibkr_paper_snapshot.ingest_ibkr_option_fills",adapter):
        with pytest.raises(ValueError,match="ibkr_paper_account_required"):
            await ingest_paper_execution_snapshot(session(),expected_account_id="U123",organization_id=None)
    adapter.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambiguous_session_accounts_rejected():
    with pytest.raises(ValueError,match="broker_session_account_mismatch"):
        await ingest_paper_execution_snapshot(session(("DU123","DU456")),expected_account_id="DU123",organization_id=None)


@pytest.mark.asyncio
async def test_disconnected_session_rejected():
    with pytest.raises(ValueError,match="broker_session_disconnected"):
        await ingest_paper_execution_snapshot(session(connected=False),expected_account_id="DU123",organization_id=None)
