"""
Tests for _adopt_untracked_positions — auto-adopts a live broker equity
position with no matching DB Trade row into the trades table, so it stops
being invisible to every DB-derived guardrail (see
execution_portfolio_gate.py's live-broker-count fix, the sibling half of
this same 2026-08-26 incident response).

Run with: pytest tests/test_adopt_untracked_positions.py -v
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Batch E2 steps 4 and 6 split each trade-touching worker into a sweep that
# resolves (broker, owner) pairs and a body that does the work for one pair.
# These tests exercise the body, which is what they always exercised: one
# broker against the rows the session returns. `single_operator_work` builds
# the pair they were implicitly using. The sweep has its own tests against
# real rows in test_worker_scope_pg.py.
from conftest import single_operator_work


def _equity_position(symbol, quantity, avg_cost=100.0):
    return SimpleNamespace(symbol=symbol, quantity=quantity, avg_cost=Decimal(str(avg_cost)))


def _open_trade(underlying):
    return SimpleNamespace(underlying=underlying)


def _adopt_session(open_trades):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    result = MagicMock()
    result.scalars.return_value = MagicMock(all=lambda: open_trades)
    session.execute = AsyncMock(return_value=result)

    begin_cm = AsyncMock()
    begin_cm.__aenter__ = AsyncMock(return_value=None)
    begin_cm.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_cm)
    session.add = MagicMock()
    return session


@pytest.mark.asyncio
async def test_adopts_genuinely_untracked_long_equity_position():
    from app.main import _adopt_untracked_positions

    broker = MagicMock()
    broker.get_equity_positions = AsyncMock(return_value=[_equity_position("MRVL", 599, 82.50)])
    session = _adopt_session(open_trades=[])

    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.services.observability.observability") as mock_obs:
        await _adopt_untracked_positions(["MRVL"], scoped=single_operator_work(broker))

    session.add.assert_called_once()
    trade = session.add.call_args.args[0]
    assert trade.strategy == "adopted_untracked"
    assert trade.underlying == "MRVL"
    assert trade.spread_type == "equity_long"
    assert trade.quantity == 599
    assert trade.credit_received == Decimal("82.50")
    assert trade.short_strike == Decimal("82.50")
    assert trade.long_strike == Decimal("82.50")
    assert trade.status == "open"
    mock_obs.incr.assert_called_once_with("reconciliation.auto_adopted")
    mock_obs.event.assert_called_once()
    assert mock_obs.event.call_args.args[0] == "reconciliation_auto_adopted"
    assert mock_obs.event.call_args.kwargs["tickers"] == ["MRVL"]


@pytest.mark.asyncio
async def test_adopts_short_equity_position_with_negative_quantity():
    from app.main import _adopt_untracked_positions

    broker = MagicMock()
    broker.get_equity_positions = AsyncMock(return_value=[_equity_position("SNDK", -24, 45.0)])
    session = _adopt_session(open_trades=[])

    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.services.observability.observability"):
        await _adopt_untracked_positions(["SNDK"], scoped=single_operator_work(broker))

    trade = session.add.call_args.args[0]
    assert trade.spread_type == "equity_short"
    assert trade.quantity == 24  # stored as a positive magnitude, direction lives in spread_type


@pytest.mark.asyncio
async def test_skips_ticker_already_covered_by_concurrent_open_trade():
    """A legitimate entry could land between the reconciler's read and this
    write — the same-session re-check must prevent a duplicate row."""
    from app.main import _adopt_untracked_positions

    broker = MagicMock()
    broker.get_equity_positions = AsyncMock(return_value=[_equity_position("AAPL", 10, 150.0)])
    session = _adopt_session(open_trades=[_open_trade("AAPL")])

    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.services.observability.observability") as mock_obs:
        await _adopt_untracked_positions(["AAPL"], scoped=single_operator_work(broker))

    session.add.assert_not_called()
    mock_obs.incr.assert_not_called()


@pytest.mark.asyncio
async def test_skips_ticker_not_found_among_live_equity_positions():
    """An untracked ticker the reconciler saw at the broker but that isn't a
    live equity position is assumed to be an options position — leg-pairing
    can't be reconstructed from a bare ticker, so it's left for manual
    review rather than guessed at."""
    from app.main import _adopt_untracked_positions

    broker = MagicMock()
    broker.get_equity_positions = AsyncMock(return_value=[_equity_position("AAPL", 10, 150.0)])
    session = _adopt_session(open_trades=[])

    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.services.observability.observability") as mock_obs:
        await _adopt_untracked_positions(["SPY"], scoped=single_operator_work(broker))  # not among live equity positions

    session.add.assert_not_called()
    mock_obs.incr.assert_not_called()


@pytest.mark.asyncio
async def test_broker_fetch_failure_adopts_nothing_and_does_not_raise():
    from app.main import _adopt_untracked_positions

    broker = MagicMock()
    broker.get_equity_positions = AsyncMock(side_effect=Exception("ibkr unavailable"))

    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.services.observability.observability") as mock_obs:
        await _adopt_untracked_positions(["MRVL"], scoped=single_operator_work(broker))  # must not raise

    mock_obs.incr.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_positions_calls_adopt_when_untracked_and_flag_on():
    from app.main import _reconcile_positions

    mock_reconciler = MagicMock()
    mock_reconciler.check = AsyncMock(return_value=SimpleNamespace(
        clean=False, untracked_at_broker=["MRVL"], phantom_in_db=[], warnings=[],
        quantity_mismatch_tickers=[],
    ))

    with patch("app.services.worker_scope.scopes_with_open_trades",
               new=AsyncMock(return_value=[None])), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()), \
         patch("app.services.position_reconciler.PositionReconciler", return_value=mock_reconciler), \
         patch("app.services.observability.observability"), \
         patch("app.core.config.settings.reconciliation_auto_adopt_untracked", True), \
         patch("app.main._adopt_untracked_positions", new=AsyncMock()) as mock_adopt:
        await _reconcile_positions()

    assert mock_adopt.await_count == 1
    assert mock_adopt.await_args.args[0] == ["MRVL"]
    # the pair the sweep ran for travels with the call
    assert mock_adopt.await_args.kwargs['scoped'].organization_id is None


@pytest.mark.asyncio
async def test_reconcile_positions_skips_adopt_when_flag_off():
    from app.main import _reconcile_positions

    mock_reconciler = MagicMock()
    mock_reconciler.check = AsyncMock(return_value=SimpleNamespace(
        clean=False, untracked_at_broker=["MRVL"], phantom_in_db=[], warnings=[],
        quantity_mismatch_tickers=[],
    ))

    with patch("app.services.worker_scope.scopes_with_open_trades",
               new=AsyncMock(return_value=[None])), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()), \
         patch("app.services.position_reconciler.PositionReconciler", return_value=mock_reconciler), \
         patch("app.services.observability.observability"), \
         patch("app.core.config.settings.reconciliation_auto_adopt_untracked", False), \
         patch("app.main._adopt_untracked_positions", new=AsyncMock()) as mock_adopt:
        await _reconcile_positions()

    mock_adopt.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_positions_skips_adopt_when_nothing_untracked():
    from app.main import _reconcile_positions

    mock_reconciler = MagicMock()
    mock_reconciler.check = AsyncMock(return_value=SimpleNamespace(
        clean=False, untracked_at_broker=[], phantom_in_db=["ghost-id"], warnings=[],
        quantity_mismatch_tickers=[],
    ))

    with patch("app.services.worker_scope.scopes_with_open_trades",
               new=AsyncMock(return_value=[None])), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()), \
         patch("app.services.position_reconciler.PositionReconciler", return_value=mock_reconciler), \
         patch("app.services.observability.observability"), \
         patch("app.main._adopt_untracked_positions", new=AsyncMock()) as mock_adopt:
        await _reconcile_positions()

    mock_adopt.assert_not_called()
