"""The poll loop hands the broker's state to the claim reconciler — every cycle,
including the cycle where there is nothing else to reconcile.

An unknown claim with no trade row at all is the case this exists for, and it is
exactly the case `_poll_fills_for_scope` used to return early on. If the hook
sat after that return it would never fire for it, and the claim would wait for
a person again. So one test pins WHERE it runs, not only that it runs.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

COUNTS = {
    "released_position_tracked": 0, "released_broker_flat": 0,
    "still_unresolved": 0, "too_fresh": 0, "indeterminate": 0, "unreachable": 0,
}


def _position(underlying, symbol=None):
    return SimpleNamespace(underlying=underlying, symbol=symbol or underlying)


def _trade(underlying):
    return SimpleNamespace(underlying=underlying)


@pytest.mark.asyncio
async def test_hook_passes_the_brokers_state_and_the_owners_scope():
    from app.main import _release_claims_on_broker_evidence

    broker = MagicMock()
    broker.confirm_symbol_flat = AsyncMock()
    scoped = SimpleNamespace(organization_id=None, broker=broker)
    with patch("app.services.position_claim.reconcile_from_evidence",
               new=AsyncMock(return_value=dict(COUNTS))) as recon:
        await _release_claims_on_broker_evidence(
            scoped, broker,
            # An option's `symbol` is its OCC-style local symbol; the
            # underlying is what a claim is keyed on.
            [_position("aapl", "AAPL 241220C00150000"), _position("MSFT")],
            [_trade("aapl")], [_trade("nvda")],
        )
    kwargs = recon.await_args.kwargs
    assert kwargs["scope"] == "global"            # single-operator scope
    assert kwargs["held"] == {"AAPL", "MSFT"}     # underlyings, upper-cased
    assert kwargs["tracked_open"] == {"AAPL"}
    assert kwargs["tracked_pending"] == {"NVDA"}
    assert kwargs["confirm_flat"] is broker.confirm_symbol_flat


@pytest.mark.asyncio
async def test_hook_never_raises_into_position_reconciliation():
    from app.main import _release_claims_on_broker_evidence

    broker = MagicMock()
    scoped = SimpleNamespace(organization_id=None, broker=broker)
    with patch("app.services.position_claim.reconcile_from_evidence",
               new=AsyncMock(side_effect=RuntimeError("db down"))):
        await _release_claims_on_broker_evidence(scoped, broker, [], [], [])  # no raise


def _session_returning(trades):
    result = MagicMock()
    result.scalars.return_value.all.return_value = trades
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=result)
    return session


def _connected_broker(positions):
    broker = MagicMock()
    broker._connected = True
    broker.ib.isConnected.return_value = True
    broker.get_positions = AsyncMock(return_value=positions)
    return broker


@pytest.mark.asyncio
async def test_it_runs_even_when_there_are_no_trade_rows_at_all():
    """No open and no pending trade: the early return follows, and the claim
    pass must already have run."""
    from app.main import _poll_fills_for_scope

    broker = _connected_broker([])
    scoped = SimpleNamespace(organization_id=None, broker=broker)
    with patch("app.core.database.AsyncSessionLocal", return_value=_session_returning([])), \
         patch("app.main._release_claims_on_broker_evidence", new=AsyncMock()) as hook:
        await _poll_fills_for_scope(scoped)
    hook.assert_awaited_once()
    args = hook.await_args.args
    assert args[1] is broker and args[3] == [] and args[4] == []


@pytest.mark.asyncio
async def test_it_does_not_run_on_a_disconnected_broker():
    """A hollow position list from a dropped connection must not be treated as
    evidence of anything: the existing connection guard returns first."""
    from app.main import _poll_fills_for_scope

    broker = _connected_broker([])
    broker.ib.isConnected.return_value = False
    scoped = SimpleNamespace(organization_id=None, broker=broker)
    with patch("app.core.database.AsyncSessionLocal", return_value=_session_returning([])), \
         patch("app.main._release_claims_on_broker_evidence", new=AsyncMock()) as hook:
        await _poll_fills_for_scope(scoped)
    hook.assert_not_awaited()
    broker.get_positions.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_stalled_pass_is_cut_off_instead_of_eating_the_polls_budget():
    """The fill poll has a 20 s budget. A gateway that never answers must cost
    this pass its own few seconds and no more — and must not raise."""
    import asyncio
    from app import main
    from app.main import _release_claims_on_broker_evidence

    async def never_returns(**kwargs):
        await asyncio.sleep(30)

    broker = MagicMock()
    scoped = SimpleNamespace(organization_id=None, broker=broker)
    with patch.object(main, "_CLAIM_EVIDENCE_BUDGET_SECONDS", 0.05), \
         patch("app.services.position_claim.reconcile_from_evidence", new=never_returns):
        started = asyncio.get_running_loop().time()
        await _release_claims_on_broker_evidence(scoped, broker, [], [], [])
        elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 2.0
