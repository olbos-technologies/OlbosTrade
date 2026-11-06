"""The reconciler must not adopt a position that already has a trade waiting for its fill.

A pending trade is an entry the desk placed whose fill has not been recorded. The broker already
shows the position, so the reconciler reads it as "untracked at broker". The fill poller promotes
the pending trade to open (or cancels it) on its next pass. If the reconciler adopts the position
first, the position gets a SECOND row: double-counted P&L and risk, two sets of exit logic.

The scheduler runs the poller before the reconciler in each tick, but the poller has a 20 s
timeout, so a slow cycle can hand the reconciler a pending trade the poller never resolved. This
is the first time both workers run for the operator organization, so the order matters now.

Real PostgreSQL, for the reason the other worker-scope suites give: the thing under test is a
filter applied to rows that exist, and a mocked session returns whatever it is told to.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.models.trade import Trade
from test_worker_scope_pg import (  # noqa: F401  (db is a fixture)
    ORG_A, ORG_B, _broker, _position, _scoped, _seed, db, pytestmark,
)


async def _rows(db, ticker):
    async with db() as s:
        return (await s.execute(select(Trade).where(Trade.underlying == ticker))).scalars().all()


async def _adopt(tickers, org, positions):
    from app.main import _adopt_untracked_positions

    obs = MagicMock()
    with patch("app.services.observability.observability", obs):
        await _adopt_untracked_positions(tickers, scoped=_scoped(org, _broker(positions)))
    return obs


async def test_a_position_with_a_pending_trade_is_not_adopted_a_second_time(db):
    await _seed(db, ORG_A, "HONA", status="pending")
    await _adopt(["HONA"], ORG_A, [_position("HONA", 5, 20.0)])
    rows = await _rows(db, "HONA")
    assert len(rows) == 1 and rows[0].status == "pending", [(r.strategy, r.status) for r in rows]


async def test_skipping_a_pending_position_is_counted_so_it_is_visible(db):
    await _seed(db, ORG_A, "HONA", status="pending")
    obs = await _adopt(["HONA"], ORG_A, [_position("HONA", 5, 20.0)])
    assert any(c.args == ("reconciliation.adopt_skipped_pending",) for c in obs.incr.call_args_list)
    assert not any(c.args == ("reconciliation.auto_adopted",) for c in obs.incr.call_args_list)


async def test_an_open_trade_still_prevents_adoption(db):
    """Regression: the original guard, unchanged."""
    await _seed(db, ORG_A, "OPENED", status="open")
    await _adopt(["OPENED"], ORG_A, [_position("OPENED", 5, 20.0)])
    assert len(await _rows(db, "OPENED")) == 1


async def test_a_genuinely_untracked_position_is_still_adopted(db):
    """The control: the guard must not have switched adoption off."""
    obs = await _adopt(["NEWPOS"], ORG_A, [_position("NEWPOS", 5, 20.0)])
    rows = await _rows(db, "NEWPOS")
    assert len(rows) == 1 and rows[0].strategy == "adopted_untracked" and rows[0].status == "open"
    assert any(c.args == ("reconciliation.auto_adopted",) for c in obs.incr.call_args_list)


@pytest.mark.parametrize("dead_status", ["closed", "cancelled"])
async def test_a_finished_trade_does_not_block_adoption(db, dead_status):
    """A closed or cancelled row means nothing is waiting: the position really is untracked."""
    await _seed(db, ORG_A, "OLDTKR", status=dead_status)
    await _adopt(["OLDTKR"], ORG_A, [_position("OLDTKR", 5, 20.0)])
    rows = await _rows(db, "OLDTKR")
    assert {(r.status, r.strategy) for r in rows} == {(dead_status, "bull_put_spread"), ("open", "adopted_untracked")}


async def test_another_owners_pending_trade_does_not_block_this_owners_adoption(db):
    """The guard is scoped like the open-trade guard: ORG_B waiting on a fill is not ORG_A's business."""
    await _seed(db, ORG_B, "SHARED", status="pending")
    await _adopt(["SHARED"], ORG_A, [_position("SHARED", 5, 20.0)])
    assert {r.organization_id for r in await _rows(db, "SHARED")} == {ORG_A, ORG_B}


async def test_the_operators_unattributed_pending_trade_blocks_adoption_under_a_designation(db):
    """With OPERATOR_ORGANIZATION_ID set, unattributed rows ARE the operator's, so their pending
    trade must count (this is the scope merge from #228)."""
    await _seed(db, None, "LEGACY", status="pending")
    with patch("app.services.trade_scope.designated_operator_id", return_value=ORG_A):
        await _adopt(["LEGACY"], ORG_A, [_position("LEGACY", 5, 20.0)])
    rows = await _rows(db, "LEGACY")
    assert len(rows) == 1 and rows[0].status == "pending"


async def test_without_a_designation_an_unattributed_pending_trade_is_not_this_orgs(db):
    await _seed(db, None, "LEGACY", status="pending")
    with patch("app.services.trade_scope.designated_operator_id", return_value=None):
        await _adopt(["LEGACY"], ORG_A, [_position("LEGACY", 5, 20.0)])
    assert len(await _rows(db, "LEGACY")) == 2


async def test_in_a_mixed_batch_only_the_pending_ticker_is_skipped(db):
    await _seed(db, ORG_A, "PEND", status="pending")
    await _adopt(["PEND", "FREE"], ORG_A, [_position("PEND", 5, 20.0), _position("FREE", 3, 10.0)])
    assert len(await _rows(db, "PEND")) == 1
    free = await _rows(db, "FREE")
    assert len(free) == 1 and free[0].strategy == "adopted_untracked"


async def test_a_lowercase_ticker_is_still_adopted_when_nothing_is_pending(db):
    """Pins the normalisation itself: the position lookup is by upper-case symbol, so an
    un-normalised ticker would silently skip a genuinely untracked position."""
    await _adopt(["freeone"], ORG_A, [_position("FREEONE", 5, 20.0)])
    rows = await _rows(db, "FREEONE")
    assert len(rows) == 1 and rows[0].strategy == "adopted_untracked"


async def test_ticker_case_does_not_defeat_the_pending_guard(db):
    await _seed(db, ORG_A, "HONA", status="pending")
    await _adopt(["hona"], ORG_A, [_position("HONA", 5, 20.0)])
    assert len(await _rows(db, "HONA")) == 1


# ── the incident sequence, both orders, with the real poller ─────────────────

async def _poll(org, positions):
    from app.main import _poll_fills_for_scope

    await _poll_fills_for_scope(_scoped(org, _broker(positions)))


async def test_adopt_first_then_poll_leaves_exactly_one_open_trade(db):
    """The slow-poller case: the reconciler runs while the trade is still pending."""
    await _seed(db, ORG_A, "HONA", status="pending", qty=3)
    positions = [_position("HONA", 7, 20.0)]
    await _adopt(["HONA"], ORG_A, positions)
    await _poll(ORG_A, positions)
    rows = await _rows(db, "HONA")
    assert len(rows) == 1, [(r.strategy, r.status) for r in rows]
    assert rows[0].status == "open" and rows[0].strategy == "bull_put_spread"
    assert rows[0].quantity == 7                       # the poller also corrected the quantity to the broker's


async def test_poll_first_then_adopt_leaves_exactly_one_open_trade(db):
    """The normal order: the poller promotes the trade, so there is nothing left to adopt."""
    await _seed(db, ORG_A, "HONA", status="pending", qty=3)
    positions = [_position("HONA", 7, 20.0)]
    await _poll(ORG_A, positions)
    await _adopt(["HONA"], ORG_A, positions)
    rows = await _rows(db, "HONA")
    assert len(rows) == 1 and rows[0].status == "open" and rows[0].strategy == "bull_put_spread"
