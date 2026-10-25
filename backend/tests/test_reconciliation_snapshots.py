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


@pytest.mark.asyncio
@pytest.mark.parametrize("phantom", [False, True], ids=["empty-clean", "db-only-warning"])
async def test_operator_reconciliation_reports_and_persists_consistent_status(phantom):
    """Exercise route -> real reconciler -> real snapshot construction.

    Only external DB/broker I/O and caller-scope resolution are mocked. A
    warning must never be serialized or saved with clean=True.
    """
    from types import SimpleNamespace
    from app.api.routes.risk import run_reconciliation

    trade = SimpleNamespace(underlying="SPY", quantity=1)
    session = MagicMock()
    session.execute = AsyncMock(return_value=_db_result_with_trades([trade] if phantom else []))
    session.commit = AsyncMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[])
    broker.place_order = AsyncMock()
    broker.place_equity_order = AsyncMock()

    with patch("app.core.database.AsyncSessionLocal", factory), \
         patch("app.services.position_reconciler.AsyncSessionLocal", factory), \
         patch("app.api.routes.risk.scope_or_401", new=AsyncMock(return_value=None)), \
         patch("app.services.worker_scope.broker_for_scope",
               new=AsyncMock(return_value=SimpleNamespace(broker=broker))) as resolve:
        response = await run_reconciliation(MagicMock())

    expected_status = "warning" if phantom else "clean"
    assert response["status"] == expected_status
    assert response["result"]["clean"] is (not phantom)
    assert response["result"]["phantom_in_db"] == (["SPY"] if phantom else [])
    assert bool(response["result"]["warnings"]) is phantom
    resolve.assert_awaited_once_with(session, None)
    broker.get_positions.assert_awaited_once()
    session.add.assert_called_once()
    snapshot = session.add.call_args.args[0]
    assert snapshot.status == expected_status
    assert snapshot.clean is (not phantom)
    assert snapshot.phantom_in_db == response["result"]["phantom_in_db"]
    assert snapshot.warnings == response["result"]["warnings"]
    assert snapshot.source == "operator"
    session.commit.assert_awaited_once()
    broker.place_order.assert_not_awaited()
    broker.place_equity_order.assert_not_awaited()
