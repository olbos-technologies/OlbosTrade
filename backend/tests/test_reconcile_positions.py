"""
Tests for _reconcile_positions — the periodic broker/DB reconciliation
check wired into the background scheduler. position_reconciler.py's own
docstring describes it as running "on every startup and before every
signal cycle," but nothing previously called it automatically; it was only
reachable via an on-demand API route. These tests cover the new automatic
wiring's alert-only behavior — confirmed with the user not to auto-engage
the kill switch, since no existing code in this app auto-halts trading
from a background check.

Run with: pytest tests/test_reconcile_positions.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _result(clean: bool, **kwargs) -> SimpleNamespace:
    return SimpleNamespace(
        clean=clean,
        untracked_at_broker=kwargs.get("untracked_at_broker", []),
        phantom_in_db=kwargs.get("phantom_in_db", []),
        warnings=kwargs.get("warnings", []),
        quantity_mismatch_tickers=kwargs.get("quantity_mismatch_tickers", []),
    )


@pytest.mark.asyncio
async def test_clean_reconciliation_does_not_alert():
    from app.main import _reconcile_positions

    mock_reconciler = MagicMock()
    mock_reconciler.check = AsyncMock(return_value=_result(clean=True))

    with patch("app.services.worker_scope.scopes_with_open_trades",
               new=AsyncMock(return_value=[None])), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()), \
         patch("app.services.position_reconciler.PositionReconciler", return_value=mock_reconciler), \
         patch("app.services.observability.observability") as mock_obs:
        await _reconcile_positions()

    mock_obs.incr.assert_not_called()
    mock_obs.event.assert_not_called()


@pytest.mark.asyncio
async def test_untracked_broker_position_alerts_but_does_not_engage_kill_switch():
    """A ghost position at the broker must be logged critically and
    recorded in observability — but never auto-engage the kill switch,
    per the confirmed alert-only design."""
    from app.main import _reconcile_positions

    mock_reconciler = MagicMock()
    mock_reconciler.check = AsyncMock(
        return_value=_result(clean=False, untracked_at_broker=["AAPL"])
    )

    with patch("app.services.worker_scope.scopes_with_open_trades",
               new=AsyncMock(return_value=[None])), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()), \
         patch("app.services.position_reconciler.PositionReconciler", return_value=mock_reconciler), \
         patch("app.services.observability.observability") as mock_obs, \
         patch("app.services.kill_switch.kill_switch_service") as mock_ks:
        await _reconcile_positions()

    mock_obs.incr.assert_called_once_with("reconciliation.mismatch")
    mock_obs.event.assert_called_once()
    assert mock_obs.event.call_args.args[0] == "reconciliation_mismatch"
    assert mock_obs.event.call_args.kwargs["untracked_at_broker"] == ["AAPL"]
    mock_ks.engage.assert_not_called()


@pytest.mark.asyncio
async def test_phantom_in_db_also_alerts():
    from app.main import _reconcile_positions

    mock_reconciler = MagicMock()
    mock_reconciler.check = AsyncMock(
        return_value=_result(clean=False, phantom_in_db=["ghost-trade-id"])
    )

    with patch("app.services.worker_scope.scopes_with_open_trades",
               new=AsyncMock(return_value=[None])), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()), \
         patch("app.services.position_reconciler.PositionReconciler", return_value=mock_reconciler), \
         patch("app.services.observability.observability") as mock_obs:
        await _reconcile_positions()

    mock_obs.incr.assert_called_once_with("reconciliation.mismatch")
