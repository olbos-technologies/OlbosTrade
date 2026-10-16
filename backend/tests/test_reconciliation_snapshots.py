import pytest
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.broker.broker_interface import Position
from app.services.position_reconciler import PositionReconciler, ReconciliationError


def make_position(symbol: str, underlying: str, qty: int = -1) -> Position:
    return Position(
        symbol=symbol,
        underlying=underlying,
        strike=Decimal("450"),
        expiration=date(2024, 1, 19),
        option_type="put",
        quantity=qty,
        avg_cost=Decimal("1.25"),
    )


def _db_result_with_trades(trades):
    return MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=trades)))
    )


@pytest.mark.asyncio
async def test_reconcile_and_record_persists_clean_snapshot():
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[])
    reconciler = PositionReconciler(broker, organization_id=None)

    with patch("app.services.position_reconciler.AsyncSessionLocal") as mock_db:
        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=_db_result_with_trades([]))
        mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch.object(reconciler, "_persist_snapshot", AsyncMock()) as persist:
            result = await reconciler.reconcile_and_record(source="cycle_preflight")

    assert result.clean is True
    persist.assert_awaited_once()
    kwargs = persist.await_args.kwargs
    assert kwargs["status"] == "clean"
    assert kwargs["source"] == "cycle_preflight"
    assert kwargs["result"].clean is True


@pytest.mark.asyncio
async def test_reconcile_and_record_persists_error_snapshot_on_drift():
    broker = MagicMock()
    broker.get_positions = AsyncMock(
        return_value=[make_position("SPY240119P00450000", "SPY")]
    )
    reconciler = PositionReconciler(broker, organization_id=None)

    with patch("app.services.position_reconciler.AsyncSessionLocal") as mock_db:
        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=_db_result_with_trades([]))
        mock_db.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_db.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch.object(reconciler, "_persist_snapshot", AsyncMock()) as persist:
            with pytest.raises(ReconciliationError, match="UNTRACKED"):
                await reconciler.reconcile_and_record(source="operator")

    persist.assert_awaited_once()
    kwargs = persist.await_args.kwargs
    assert kwargs["status"] == "error"
    assert kwargs["source"] == "operator"
    assert "UNTRACKED" in kwargs["error_message"]
    assert kwargs["result"] is not None
    assert kwargs["result"].untracked_at_broker == ["SPY"]
