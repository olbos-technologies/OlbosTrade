"""
Tests for _correct_quantity_mismatches — corrects a tracked Trade row's
quantity to match the broker's live quantity when the reconciler finds a
disagreement. Sibling fix to _adopt_untracked_positions (see
test_adopt_untracked_positions.py): confirmed live 2026-08-26 that MRVL/SNDK
Trade rows still held stale quantities from before the close_equity_trade()
sizing bug was fixed (commit 1cc3eb8) -- that fix stops new mismatches, it
never retroactively corrected rows it had already damaged.

Run with: pytest tests/test_correct_quantity_mismatches.py -v
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


def _open_trade(underlying, quantity=86, spread_type="equity_long", trade_id="t1"):
    return SimpleNamespace(id=trade_id, underlying=underlying, quantity=quantity, spread_type=spread_type)


def _correction_session(open_trades):
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
    return session


@pytest.mark.asyncio
async def test_corrects_quantity_for_single_open_equity_row():
    from app.main import _correct_quantity_mismatches

    trade = _open_trade("MRVL", quantity=86, spread_type="equity_long")
    broker = MagicMock()
    broker.get_equity_positions = AsyncMock(return_value=[_equity_position("MRVL", 599)])
    session = _correction_session([trade])

    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.services.observability.observability") as mock_obs:
        await _correct_quantity_mismatches(["MRVL"], scoped=single_operator_work(broker))

    assert trade.quantity == 599
    mock_obs.incr.assert_called_once_with("reconciliation.quantity_corrected")
    mock_obs.event.assert_called_once()
    assert mock_obs.event.call_args.args[0] == "reconciliation_quantity_corrected"
    assert mock_obs.event.call_args.kwargs["tickers"] == ["MRVL"]


@pytest.mark.asyncio
async def test_corrects_short_position_quantity_as_positive_magnitude():
    from app.main import _correct_quantity_mismatches

    trade = _open_trade("SNDK", quantity=12, spread_type="equity_short")
    broker = MagicMock()
    broker.get_equity_positions = AsyncMock(return_value=[_equity_position("SNDK", -24)])
    session = _correction_session([trade])

    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.services.observability.observability"):
        await _correct_quantity_mismatches(["SNDK"], scoped=single_operator_work(broker))

    assert trade.quantity == 24  # magnitude only, side stays in spread_type


@pytest.mark.asyncio
async def test_skips_ticker_with_multiple_open_equity_rows():
    """Ambiguous which row the broker's single summed quantity belongs to."""
    from app.main import _correct_quantity_mismatches

    t1 = _open_trade("MRVL", quantity=50, trade_id="t1")
    t2 = _open_trade("MRVL", quantity=36, trade_id="t2")
    broker = MagicMock()
    broker.get_equity_positions = AsyncMock(return_value=[_equity_position("MRVL", 599)])
    session = _correction_session([t1, t2])

    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.services.observability.observability") as mock_obs:
        await _correct_quantity_mismatches(["MRVL"], scoped=single_operator_work(broker))

    assert t1.quantity == 50 and t2.quantity == 36  # untouched
    mock_obs.incr.assert_not_called()


@pytest.mark.asyncio
async def test_skips_ticker_with_no_open_equity_rows():
    from app.main import _correct_quantity_mismatches

    broker = MagicMock()
    broker.get_equity_positions = AsyncMock(return_value=[_equity_position("MRVL", 599)])
    session = _correction_session([])

    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.services.observability.observability") as mock_obs:
        await _correct_quantity_mismatches(["MRVL"], scoped=single_operator_work(broker))

    mock_obs.incr.assert_not_called()


@pytest.mark.asyncio
async def test_skips_ticker_not_found_among_live_equity_positions():
    """An options-position quantity mismatch -- out of scope, left for manual review."""
    from app.main import _correct_quantity_mismatches

    trade = _open_trade("SPY", quantity=1, spread_type="put")
    broker = MagicMock()
    broker.get_equity_positions = AsyncMock(return_value=[_equity_position("AAPL", 10)])
    session = _correction_session([trade])

    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.services.observability.observability") as mock_obs:
        await _correct_quantity_mismatches(["SPY"], scoped=single_operator_work(broker))

    assert trade.quantity == 1  # untouched
    mock_obs.incr.assert_not_called()


@pytest.mark.asyncio
async def test_ignores_options_row_even_when_ticker_matches_a_live_equity_symbol():
    from app.main import _correct_quantity_mismatches

    trade = _open_trade("SPY", quantity=1, spread_type="put")
    broker = MagicMock()
    broker.get_equity_positions = AsyncMock(return_value=[_equity_position("SPY", 100)])
    session = _correction_session([trade])

    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.services.observability.observability") as mock_obs:
        await _correct_quantity_mismatches(["SPY"], scoped=single_operator_work(broker))

    assert trade.quantity == 1  # options row never touched
    mock_obs.incr.assert_not_called()


@pytest.mark.asyncio
async def test_broker_fetch_failure_corrects_nothing_and_does_not_raise():
    from app.main import _correct_quantity_mismatches

    broker = MagicMock()
    broker.get_equity_positions = AsyncMock(side_effect=Exception("ibkr unavailable"))

    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.services.observability.observability") as mock_obs:
        await _correct_quantity_mismatches(["MRVL"], scoped=single_operator_work(broker))  # must not raise

    mock_obs.incr.assert_not_called()


@pytest.mark.asyncio
async def test_reconcile_positions_calls_correct_when_mismatch_and_flag_on():
    from app.main import _reconcile_positions

    mock_reconciler = MagicMock()
    mock_reconciler.check = AsyncMock(return_value=SimpleNamespace(
        clean=False, untracked_at_broker=[], phantom_in_db=[],
        warnings=["QUANTITY MISMATCH MRVL: broker=599 db=86"],
        quantity_mismatch_tickers=["MRVL"],
    ))

    with patch("app.services.worker_scope.scopes_with_open_trades",
               new=AsyncMock(return_value=[None])), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()), \
         patch("app.services.position_reconciler.PositionReconciler", return_value=mock_reconciler), \
         patch("app.services.observability.observability"), \
         patch("app.core.config.settings.reconciliation_auto_correct_quantity", True), \
         patch("app.main._correct_quantity_mismatches", new=AsyncMock()) as mock_correct:
        await _reconcile_positions()

    assert mock_correct.await_count == 1
    assert mock_correct.await_args.args[0] == ["MRVL"]
    assert mock_correct.await_args.kwargs['scoped'].organization_id is None


@pytest.mark.asyncio
async def test_reconcile_positions_skips_correct_when_flag_off():
    from app.main import _reconcile_positions

    mock_reconciler = MagicMock()
    mock_reconciler.check = AsyncMock(return_value=SimpleNamespace(
        clean=False, untracked_at_broker=[], phantom_in_db=[],
        warnings=["QUANTITY MISMATCH MRVL: broker=599 db=86"],
        quantity_mismatch_tickers=["MRVL"],
    ))

    with patch("app.services.worker_scope.scopes_with_open_trades",
               new=AsyncMock(return_value=[None])), \
         patch("app.broker.broker_factory.get_broker", return_value=MagicMock()), \
         patch("app.services.position_reconciler.PositionReconciler", return_value=mock_reconciler), \
         patch("app.services.observability.observability"), \
         patch("app.core.config.settings.reconciliation_auto_correct_quantity", False), \
         patch("app.main._correct_quantity_mismatches", new=AsyncMock()) as mock_correct:
        await _reconcile_positions()

    mock_correct.assert_not_called()
