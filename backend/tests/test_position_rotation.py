"""Unit tests for position rotation selection + config wiring."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import rotation_correlation_cache
from app.services.position_rotation import (
    RotationCandidate,
    close_equity_trade,
    close_options_trade,
    select_rotation_targets,
    rotate_for_blocked_entry,
    _unrealized_by_position,
)


@pytest.fixture(autouse=True)
def _reset_correlation_cache():
    rotation_correlation_cache.clear()
    yield
    rotation_correlation_cache.clear()


def _c(
    tid: str,
    underlying: str,
    pnl: float,
    conf: Optional[float] = None,
    *,
    quality: Optional[float] = None,
    in_cluster: Optional[bool] = None,
    days_ago: int = 0,
    spread: str = "equity_long",
) -> RotationCandidate:
    entry = datetime(2026, 8, 20, tzinfo=timezone.utc)
    if days_ago:
        from datetime import timedelta
        entry = entry - timedelta(days=days_ago)
    return RotationCandidate(
        trade_id=tid,
        underlying=underlying,
        unrealized_pnl=pnl,
        confidence=conf,
        entry_date=entry,
        spread_type=spread,
        quality_score=quality,
        in_flagged_cluster=in_cluster,
    )


def test_never_selects_profitable_position():
    """The core Winner Protection guarantee: a profitable position with the
    weakest quality score must never be picked — the old algorithm would
    have picked it first ("bank the winner")."""
    picks = select_rotation_targets(
        [
            _c("1", "AAPL", pnl=50, quality=1),      # winner, weakest thesis — must be protected
            _c("2", "MSFT", pnl=-10, quality=80),
            _c("3", "NVDA", pnl=-5, quality=60),
        ],
        incoming_ticker="TSLA",
        count=1,
    )
    assert "1" not in [p.trade_id for p in picks]
    assert [p.trade_id for p in picks] == ["3"]  # lowest quality among eligible


def test_insufficient_eligible_after_winner_protection_returns_empty():
    """Only winners are open — rotation must skip entirely (leave the new
    signal blocked) rather than touch a winner to hit the requested count."""
    picks = select_rotation_targets(
        [
            _c("1", "AAPL", pnl=100),
            _c("2", "MSFT", pnl=50),
            _c("3", "NVDA", pnl=-5),
        ],
        incoming_ticker="TSLA",
        count=2,
    )
    assert picks == []


def test_prefers_worst_quality_score_among_eligible():
    picks = select_rotation_targets(
        [
            _c("1", "AAPL", pnl=-5, quality=90),
            _c("2", "MSFT", pnl=-10, quality=20),
            _c("3", "NVDA", pnl=-3, quality=50),
        ],
        incoming_ticker="TSLA",
        count=2,
    )
    assert [p.trade_id for p in picks] == ["2", "3"]  # ascending quality


def test_cluster_membership_breaks_quality_score_tie():
    """Two candidates share the same quality_score — the one in a flagged
    correlation cluster must be picked first (tiebreaker only)."""
    picks = select_rotation_targets(
        [
            _c("1", "AAPL", pnl=-5, quality=50, in_cluster=True),
            _c("2", "MSFT", pnl=-5, quality=50, in_cluster=False),
            _c("3", "NVDA", pnl=-5, quality=90),  # clearly best quality, must survive
        ],
        incoming_ticker="TSLA",
        count=1,
    )
    assert [p.trade_id for p in picks] == ["1"]


def test_cluster_membership_never_overrides_real_quality_difference():
    """AAPL is clustered but has the *better* quality score than MSFT
    (not clustered) — quality_score must still win; the tiebreaker only
    applies within a quality tier, never across one."""
    picks = select_rotation_targets(
        [
            _c("1", "AAPL", pnl=-5, quality=80, in_cluster=True),
            _c("2", "MSFT", pnl=-5, quality=20, in_cluster=False),
        ],
        incoming_ticker="TSLA",
        count=1,
    )
    assert [p.trade_id for p in picks] == ["2"]  # worse quality wins despite not being clustered


def test_cluster_membership_none_is_neutral_not_biased_either_way():
    """Fail-open regression: with in_flagged_cluster left at its default
    (None, i.e. cache stale/missing), ranking must fall straight through
    to confidence/entry_date exactly as before this tiebreaker existed."""
    picks = select_rotation_targets(
        [
            _c("1", "AAPL", pnl=-5, conf=0.9),
            _c("2", "MSFT", pnl=-5, conf=0.2),
            _c("3", "NVDA", pnl=-5, conf=0.5),
        ],
        incoming_ticker="TSLA",
        count=2,
    )
    assert [p.trade_id for p in picks] == ["2", "3"]


def test_quality_score_missing_falls_back_to_confidence():
    picks = select_rotation_targets(
        [
            _c("1", "AAPL", pnl=-5, conf=0.9),
            _c("2", "MSFT", pnl=-5, conf=0.2),
            _c("3", "NVDA", pnl=-5, conf=0.5),
        ],
        incoming_ticker="TSLA",
        count=2,
    )
    assert [p.trade_id for p in picks] == ["2", "3"]  # ascending confidence


def test_quality_and_confidence_missing_falls_back_to_oldest_entry_date():
    picks = select_rotation_targets(
        [
            _c("1", "AAPL", pnl=-5, days_ago=1),
            _c("2", "MSFT", pnl=-5, days_ago=10),
            _c("3", "NVDA", pnl=-5, days_ago=5),
        ],
        incoming_ticker="TSLA",
        count=2,
    )
    assert [p.trade_id for p in picks] == ["2", "3"]  # oldest first


def test_excludes_incoming_underlying():
    picks = select_rotation_targets(
        [
            _c("1", "AAPL", pnl=-999, quality=1),
            _c("2", "MSFT", pnl=-50, quality=30),
            _c("3", "NVDA", pnl=-40, quality=70),
        ],
        incoming_ticker="AAPL",
        count=2,
    )
    assert all(p.underlying != "AAPL" for p in picks)
    assert [p.trade_id for p in picks] == ["2", "3"]


def test_returns_empty_when_not_enough_eligible_after_ticker_exclusion():
    picks = select_rotation_targets(
        [_c("1", "AAPL", pnl=-10), _c("2", "MSFT", pnl=-5)],
        incoming_ticker="AAPL",
        count=2,
    )
    assert picks == []  # only MSFT eligible after excluding AAPL


def test_skips_options_spread_types():
    picks = select_rotation_targets(
        [
            _c("1", "AAPL", pnl=100, quality=1, spread="bull_put_spread"),
            _c("2", "MSFT", pnl=-50, quality=30),
            _c("3", "NVDA", pnl=-40, quality=70),
        ],
        incoming_ticker="TSLA",
        count=2,
    )
    assert [p.trade_id for p in picks] == ["2", "3"]


def test_accepts_put_and_call_spread_types():
    """The widened candidate pool (5b) must accept real options spread_type
    values ('put'/'call') — distinct from the arbitrary-string exclusion
    test above, which uses 'bull_put_spread' (never a real Trade.spread_type
    value for options)."""
    picks = select_rotation_targets(
        [
            _c("1", "SPY", pnl=100, quality=1, spread="put"),   # winner — protected
            _c("2", "QQQ", pnl=-50, quality=30, spread="call"),
            _c("3", "IWM", pnl=-40, quality=70, spread="equity_long"),
        ],
        incoming_ticker="TSLA",
        count=2,
    )
    assert [p.trade_id for p in picks] == ["2", "3"]


@pytest.mark.asyncio
async def test_unrealized_by_position_keys_by_symbol_and_asset_class():
    positions = [
        SimpleNamespace(underlying="SPY", asset_type="option", unrealized_pnl=150.0),
        SimpleNamespace(underlying="SPY", asset_type="option", unrealized_pnl=-40.0),
        SimpleNamespace(underlying="AAPL", asset_type="equity", unrealized_pnl=999.0),
        SimpleNamespace(underlying="QQQ", asset_type="option", unrealized_pnl=None),
    ]
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=positions)

    totals = await _unrealized_by_position(broker)

    # Equity is now included — that is the point of the unification.
    assert totals == {("SPY", "options"): 110.0, ("AAPL", "equity"): 999.0}


@pytest.mark.asyncio
async def test_unrealized_by_position_fetch_failure_returns_empty():
    broker = MagicMock()
    broker.get_positions = AsyncMock(side_effect=RuntimeError("broker down"))
    totals = await _unrealized_by_position(broker)
    assert totals == {}


@pytest.mark.asyncio
async def test_rotate_respects_flag_off(monkeypatch):
    from app.core import config as cfg
    monkeypatch.setattr(cfg.settings, "position_rotation_on_max", False)
    out = await rotate_for_blocked_entry(
        incoming_ticker="TSLA", broker=MagicMock(),
    )
    assert out == []


@pytest.mark.asyncio
async def test_rotate_closes_selected_targets_and_protects_the_winner(monkeypatch):
    """End-to-end: MSFT is both the biggest winner AND the weakest
    confidence — the old algorithm would bank it first, then close it again
    on the confidence tiebreak. It must survive both rounds."""
    from app.core import config as cfg
    monkeypatch.setattr(cfg.settings, "position_rotation_on_max", True)
    monkeypatch.setattr(cfg.settings, "position_rotation_closes", 2)

    trades = [
        SimpleNamespace(
            id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1",
            underlying="AAPL", spread_type="equity_long",
            quantity=10, credit_received=100, signal_score=0.9,
            entry_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
        ),
        SimpleNamespace(
            id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2",
            underlying="MSFT", spread_type="equity_long",
            quantity=10, credit_received=100, signal_score=0.2,
            entry_date=datetime(2026, 8, 2, tzinfo=timezone.utc),
        ),
        SimpleNamespace(
            id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa3",
            underlying="NVDA", spread_type="equity_long",
            quantity=10, credit_received=100, signal_score=0.5,
            entry_date=datetime(2026, 8, 3, tzinfo=timezone.utc),
        ),
    ]

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=lambda: trades)),
    ))

    # MSFT is the biggest winner (+100); AAPL and NVDA are losses. These are
    # the same P&Ls the old mid-price fakes produced ((mid - 100) x 10), now
    # supplied the way production supplies them — the broker's own figure.
    equity_pnl = {("AAPL", "equity"): -100.0,
                  ("MSFT", "equity"): 100.0,
                  ("NVDA", "equity"): -50.0}

    async def fake_quality(ticker, _direction):
        return {"AAPL": 30.0, "MSFT": 10.0, "NVDA": 70.0}[ticker]

    closed: list[str] = []

    async def fake_close(trade, **kwargs):
        closed.append(trade.underlying)
        return {"trade_id": str(trade.id), "ticker": trade.underlying, "status": "filled"}

    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.services.position_rotation._unrealized_by_position",
               new=AsyncMock(return_value=equity_pnl)), \
         patch("app.services.alpha_edge_engine.compute_equity_hold_score", side_effect=fake_quality), \
         patch("app.services.position_rotation.close_equity_trade", side_effect=fake_close):
        out = await rotate_for_blocked_entry(
            incoming_ticker="TSLA", broker=MagicMock(),
        )

    assert "MSFT" not in closed  # the winner, even with the weakest confidence, is protected
    assert closed == ["AAPL", "NVDA"]  # ascending quality among the eligible (losing) positions
    assert len(out) == 2

    # The receipt (later persisted into ExecutionEvent.payload for the
    # rotation-performance ledger) must carry the decision-time ranking
    # signals, not just the close mechanics.
    aapl_receipt = next(r for r in out if r["ticker"] == "AAPL")
    assert aapl_receipt["quality_score"] == 30.0
    assert aapl_receipt["confidence"] == 0.9
    assert aapl_receipt["unrealized_pnl_at_decision"] == -100.0  # (90-100)*10
    assert aapl_receipt["in_flagged_cluster"] is None  # no correlation cache populated in this test


@pytest.mark.asyncio
async def test_rotate_quality_lookup_failure_falls_back_to_confidence(monkeypatch):
    from app.core import config as cfg
    monkeypatch.setattr(cfg.settings, "position_rotation_on_max", True)
    monkeypatch.setattr(cfg.settings, "position_rotation_closes", 1)

    trades = [
        SimpleNamespace(
            id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1",
            underlying="AAPL", spread_type="equity_long",
            quantity=10, credit_received=100, signal_score=0.9,
            entry_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
        ),
        SimpleNamespace(
            id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2",
            underlying="MSFT", spread_type="equity_long",
            quantity=10, credit_received=100, signal_score=0.2,
            entry_date=datetime(2026, 8, 2, tzinfo=timezone.utc),
        ),
    ]

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=lambda: trades)),
    ))

    # Both losses, as the old mid fakes produced ((mid - 100) x 10).
    equity_pnl = {("AAPL", "equity"): -100.0, ("MSFT", "equity"): -50.0}

    closed: list[str] = []

    async def fake_close(trade, **kwargs):
        closed.append(trade.underlying)
        return {"trade_id": str(trade.id), "ticker": trade.underlying, "status": "filled"}

    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.services.position_rotation._unrealized_by_position",
               new=AsyncMock(return_value=equity_pnl)), \
         patch("app.services.alpha_edge_engine.compute_equity_hold_score",
               side_effect=RuntimeError("quality lookup failed")), \
         patch("app.services.position_rotation.close_equity_trade", side_effect=fake_close):
        out = await rotate_for_blocked_entry(
            incoming_ticker="TSLA", broker=MagicMock(),
        )

    # Quality lookup failed for both -> falls back to lowest confidence (MSFT, 0.2).
    assert closed == ["MSFT"]


# ── close_equity_trade ────────────────────────────────────────────────────
# Regression tests for a real production incident (2026-08-26): several
# "closed" Trade rows still had live, non-zero positions at the broker
# months later — some flipped to the opposite side entirely — because the
# old close_equity_trade() sized/directed the closing order from the DB's
# trade.quantity/spread_type, which can silently drift from the broker's
# real holding (a partial fill, an earlier close that itself misfired,
# etc.). These tests pin the fix: side and size must come from the
# broker's own live position, never from the DB row.

def _eq_trade(spread_type="equity_long", quantity=10, underlying="AAPL"):
    return SimpleNamespace(
        id="cccccccc-cccc-cccc-cccc-ccccccccccc1",
        underlying=underlying, spread_type=spread_type, quantity=quantity,
    )


@pytest.mark.asyncio
async def test_close_equity_trade_uses_live_broker_quantity_not_db_quantity():
    """DB says 86 shares; the broker's real holding is 599 — the closing
    order must be sized 599, matching the real production MRVL mismatch
    (broker=599 db=86) the reconciler was actively flagging."""
    trade = _eq_trade(spread_type="equity_long", quantity=86)
    broker = MagicMock()
    broker.get_equity_positions = AsyncMock(
        return_value=[SimpleNamespace(symbol="AAPL", quantity=599)]
    )
    broker.cancel_open_orders = AsyncMock(return_value=0)
    broker.place_equity_order = AsyncMock(return_value=MagicMock(
        status="filled", order_id="ord-eq-1", fill_price=101.0,
    ))
    with patch("app.services.trade_recorder.trade_recorder.record_exit",
               new=AsyncMock(return_value=True)):
        out = await close_equity_trade(trade, broker=broker, closed_by="manual")

    broker.place_equity_order.assert_awaited_once()
    assert broker.place_equity_order.await_args.kwargs["qty"] == 599
    assert broker.place_equity_order.await_args.kwargs["side"] == "SELL"
    assert out["quantity"] == 599


@pytest.mark.asyncio
async def test_close_equity_trade_uses_live_side_not_db_spread_type():
    """DB still records this as equity_long (implying SELL to close), but
    the broker's live position is now negative (short) — the close must
    follow the LIVE sign (BUY to cover), not the stale DB direction. This
    is the exact mechanism that flipped COST/EXC/META to the wrong side in
    production: a stale-direction SELL oversold straight through zero."""
    trade = _eq_trade(spread_type="equity_long", quantity=21)
    broker = MagicMock()
    broker.get_equity_positions = AsyncMock(
        return_value=[SimpleNamespace(symbol="AAPL", quantity=-27)]
    )
    broker.cancel_open_orders = AsyncMock(return_value=0)
    broker.place_equity_order = AsyncMock(return_value=MagicMock(
        status="filled", order_id="ord-eq-2", fill_price=99.0,
    ))
    with patch("app.services.trade_recorder.trade_recorder.record_exit",
               new=AsyncMock(return_value=True)):
        await close_equity_trade(trade, broker=broker, closed_by="manual")

    assert broker.place_equity_order.await_args.kwargs["side"] == "BUY"
    assert broker.place_equity_order.await_args.kwargs["qty"] == 27


@pytest.mark.asyncio
async def test_close_equity_trade_already_flat_raises_without_submitting():
    """If the broker shows no live position at all for this ticker, there
    is nothing real to close — raise rather than submit a fabricated
    order sized from a stale DB quantity."""
    trade = _eq_trade(spread_type="equity_long", quantity=11)
    broker = MagicMock()
    broker.get_equity_positions = AsyncMock(return_value=[])
    broker.place_equity_order = AsyncMock()
    with pytest.raises(RuntimeError):
        await close_equity_trade(trade, broker=broker, closed_by="manual")
    broker.place_equity_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_equity_trade_no_live_match_for_ticker_raises():
    """get_equity_positions() returns real positions, just none matching
    this trade's ticker — must be treated the same as flat, not crash on
    a missing match."""
    trade = _eq_trade(spread_type="equity_long", quantity=11, underlying="AAPL")
    broker = MagicMock()
    broker.get_equity_positions = AsyncMock(
        return_value=[SimpleNamespace(symbol="MSFT", quantity=50)]
    )
    broker.place_equity_order = AsyncMock()
    with pytest.raises(RuntimeError):
        await close_equity_trade(trade, broker=broker, closed_by="manual")
    broker.place_equity_order.assert_not_awaited()


# ── close_options_trade ──────────────────────────────────────────────────

def _opt_trade(spread_type="put", short=100, long=95, qty=2, strategy="bull_put_spread"):
    return SimpleNamespace(
        id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1",
        underlying="SPY", spread_type=spread_type, strategy=strategy,
        short_strike=Decimal(str(short)), long_strike=Decimal(str(long)),
        expiration=date(2026, 9, 18), quantity=qty,
    )


def _held_legs(underlying="SPY", qty=2, short=100, long=95):
    """BOTH live legs of the spread, at the broker.

    close_options_trade() sizes the close from the live legs and refuses
    unless both are held (PR #64): the combo quantity used to come from
    trade.quantity, so DB drift could submit a 2-lot close against a 1-lot
    position — closing one and OPENING one the other way.
    """
    return [
        SimpleNamespace(
            symbol=f"{underlying} L{k}", underlying=underlying,
            strike=Decimal(str(k)), expiration=date(2026, 9, 18),
            option_type="put", quantity=sign * qty,
            avg_cost=Decimal("1.0"), asset_type="option",
        )
        for k, sign in ((short, -1), (long, 1))
    ]


def _held_option(underlying="SPY", qty=-2):
    """A single live leg — kept for callers that only need existence.

    close_options_trade() refuses to submit unless one exists (PR #64): every
    leg it builds comes from the DB row, so a closing combo for a position that
    is not held is an OPENING trade in the other direction. The tests below
    exercise what it SENDS, so they have to satisfy that precondition first —
    the refusal path itself is covered in
    test_options_close_needs_a_live_position.py.
    """
    return SimpleNamespace(
        symbol=f"{underlying} 260918P00100000", underlying=underlying,
        strike=Decimal("100"), expiration=date(2026, 9, 18), option_type="put",
        quantity=qty, avg_cost=Decimal("1.0"), asset_type="option",
    )


@pytest.mark.asyncio
async def test_close_options_trade_success_correct_leg_actions_and_mkt():
    trade = _opt_trade()
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=_held_legs())
    broker.cancel_open_orders = AsyncMock(return_value=1)
    broker.place_order = AsyncMock(return_value=MagicMock(
        status="filled", order_id="ord-opt-1", fill_price=Decimal("1.50"),
    ))
    with patch("app.services.trade_recorder.trade_recorder.record_exit",
               new=AsyncMock(return_value=True)) as record_mock:
        out = await close_options_trade(trade, broker=broker, closed_by="manual")

    broker.place_order.assert_awaited_once()
    order = broker.place_order.await_args.args[0]
    assert order.order_type == "MKT"
    assert order.limit_price == Decimal("0")
    short_leg, long_leg = order.legs
    assert short_leg.strike == Decimal("100") and short_leg.action == "BUY"
    assert long_leg.strike == Decimal("95") and long_leg.action == "SELL"
    assert short_leg.option_type == "put" and long_leg.option_type == "put"
    assert short_leg.quantity == 2 and long_leg.quantity == 2

    record_mock.assert_awaited_once()
    assert record_mock.await_args.kwargs["cost_to_close"] == 1.50
    assert record_mock.await_args.kwargs["exit_reason"] == "manual"
    assert out["status"] == "filled"
    assert out["option_type"] == "put"
    assert out["cancelled_open_orders"] == 1


@pytest.mark.asyncio
async def test_close_options_trade_broker_rejection_raises_runtimeerror():
    trade = _opt_trade()
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=_held_legs())
    broker.cancel_open_orders = AsyncMock(return_value=0)
    broker.place_order = AsyncMock(return_value=MagicMock(
        status="rejected", order_id=None, fill_price=None,
    ))
    # match= on purpose: close_options_trade now raises RuntimeError for a
    # SECOND reason (no live position at the broker), so a bare
    # pytest.raises(RuntimeError) would pass without the broker having
    # rejected anything.
    with pytest.raises(RuntimeError, match="broker rejected"):
        await close_options_trade(trade, broker=broker)
    broker.place_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_options_trade_missing_strikes_raises_valueerror():
    trade = _opt_trade()
    trade.short_strike = None
    broker = MagicMock()
    broker.place_order = AsyncMock()
    with pytest.raises(ValueError):
        await close_options_trade(trade, broker=broker)
    broker.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_options_trade_missing_expiration_raises_valueerror():
    trade = _opt_trade()
    trade.expiration = None
    broker = MagicMock()
    with pytest.raises(ValueError):
        await close_options_trade(trade, broker=broker)


@pytest.mark.asyncio
async def test_close_options_trade_non_option_spread_type_raises_valueerror():
    trade = _opt_trade(spread_type="equity_long")
    broker = MagicMock()
    with pytest.raises(ValueError):
        await close_options_trade(trade, broker=broker)


@pytest.mark.asyncio
async def test_close_options_trade_not_filled_skips_record_exit():
    trade = _opt_trade()
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=_held_legs())
    broker.cancel_open_orders = AsyncMock(return_value=0)
    broker.place_order = AsyncMock(return_value=MagicMock(
        status="submitted", order_id="ord-opt-2", fill_price=None,
    ))
    with patch("app.services.trade_recorder.trade_recorder.record_exit",
               new=AsyncMock(return_value=True)) as record_mock:
        out = await close_options_trade(trade, broker=broker)

    record_mock.assert_not_awaited()
    assert out["status"] == "submitted"


# ── rotate_for_blocked_entry — widened (options-eligible) candidate pool ──

def _open_option_row(tid, underlying, spread_type="put", signal_score=0.5,
                      days_ago=0):
    entry = datetime(2026, 8, 20, tzinfo=timezone.utc)
    if days_ago:
        entry = entry - timedelta(days=days_ago)
    return SimpleNamespace(
        id=tid, underlying=underlying, spread_type=spread_type,
        short_strike=Decimal("100"), long_strike=Decimal("95"),
        quantity=2, credit_received=Decimal("1.5"), mae_pnl=None,
        signal_score=signal_score, entry_date=entry,
    )


def _open_equity_row(tid, underlying, spread_type="equity_long",
                      signal_score=0.5):
    return SimpleNamespace(
        id=tid, underlying=underlying, spread_type=spread_type,
        quantity=10, credit_received=100, signal_score=signal_score,
        entry_date=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )


def _rotation_session(trades):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=lambda: trades)),
    ))
    return session


@pytest.mark.asyncio
async def test_rotate_closes_worst_options_position_via_close_options_trade(monkeypatch):
    from app.core import config as cfg
    monkeypatch.setattr(cfg.settings, "position_rotation_on_max", True)
    monkeypatch.setattr(cfg.settings, "position_rotation_closes", 1)

    trades = [
        _open_option_row("t1", "SPY", signal_score=0.9),
        _open_option_row("t2", "QQQ", signal_score=0.2),
    ]
    session = _rotation_session(trades)
    closed: list[str] = []

    async def fake_close(trade, **kwargs):
        closed.append(trade.underlying)
        return {"trade_id": str(trade.id), "ticker": trade.underlying, "status": "filled"}

    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.services.position_rotation._unrealized_by_position",
               new=AsyncMock(return_value={("SPY", "options"): -80.0,
                                          ("QQQ", "options"): -10.0})), \
         patch("app.services.alpha_edge_engine.compute_options_hold_score",
               side_effect=lambda t: {"SPY": 20, "QQQ": 60}[t.underlying]), \
         patch("app.services.position_rotation.close_options_trade", side_effect=fake_close) as opt_mock, \
         patch("app.services.position_rotation.close_equity_trade") as eq_mock:
        out = await rotate_for_blocked_entry(incoming_ticker="TSLA", broker=MagicMock())

    assert closed == ["SPY"]  # worse hold score among the losers
    opt_mock.assert_awaited_once()
    eq_mock.assert_not_called()
    assert len(out) == 1


@pytest.mark.asyncio
async def test_rotate_protects_profitable_options_position(monkeypatch):
    from app.core import config as cfg
    monkeypatch.setattr(cfg.settings, "position_rotation_on_max", True)
    monkeypatch.setattr(cfg.settings, "position_rotation_closes", 1)

    trades = [
        _open_option_row("t1", "SPY", signal_score=0.1),   # profitable, weakest confidence
        _open_option_row("t2", "QQQ", signal_score=0.9),   # loser
    ]
    session = _rotation_session(trades)
    closed: list[str] = []

    async def fake_close(trade, **kwargs):
        closed.append(trade.underlying)
        return {"trade_id": str(trade.id), "ticker": trade.underlying, "status": "filled"}

    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.services.position_rotation._unrealized_by_position",
               new=AsyncMock(return_value={("SPY", "options"): 200.0,
                                          ("QQQ", "options"): -30.0})), \
         patch("app.services.alpha_edge_engine.compute_options_hold_score", return_value=50), \
         patch("app.services.position_rotation.close_options_trade", side_effect=fake_close):
        out = await rotate_for_blocked_entry(incoming_ticker="TSLA", broker=MagicMock())

    assert "SPY" not in closed
    assert closed == ["QQQ"]


@pytest.mark.asyncio
async def test_rotate_fetches_broker_positions_exactly_once_for_options(monkeypatch):
    from app.core import config as cfg
    monkeypatch.setattr(cfg.settings, "position_rotation_on_max", True)
    monkeypatch.setattr(cfg.settings, "position_rotation_closes", 2)

    trades = [
        _open_option_row("t1", "SPY", signal_score=0.9),
        _open_option_row("t2", "QQQ", signal_score=0.2),
        _open_option_row("t3", "IWM", signal_score=0.5),
    ]
    session = _rotation_session(trades)
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[])

    async def fake_close(trade, **kwargs):
        return {"trade_id": str(trade.id), "ticker": trade.underlying, "status": "filled"}

    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.services.alpha_edge_engine.compute_options_hold_score", return_value=50), \
         patch("app.services.position_rotation.close_options_trade", side_effect=fake_close):
        await rotate_for_blocked_entry(incoming_ticker="TSLA", broker=broker)

    broker.get_positions.assert_awaited_once()


@pytest.mark.asyncio
async def test_rotate_mixed_pool_closes_worse_ranked_options_over_equity(monkeypatch):
    from app.core import config as cfg
    monkeypatch.setattr(cfg.settings, "position_rotation_on_max", True)
    monkeypatch.setattr(cfg.settings, "position_rotation_closes", 1)

    trades = [
        _open_equity_row("t1", "AAPL", signal_score=0.9),
        _open_option_row("t2", "SPY", signal_score=0.9),
    ]
    session = _rotation_session(trades)
    closed: list[str] = []

    async def fake_close_eq(trade, **kwargs):
        closed.append(trade.underlying)
        return {"trade_id": str(trade.id), "ticker": trade.underlying, "status": "filled"}

    async def fake_close_opt(trade, **kwargs):
        closed.append(trade.underlying)
        return {"trade_id": str(trade.id), "ticker": trade.underlying, "status": "filled"}

    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.services.alpha_edge_engine.compute_equity_hold_score", return_value=80), \
         patch("app.services.position_rotation._unrealized_by_position",
               new=AsyncMock(return_value={("SPY", "options"): -50.0,
                                           ("AAPL", "equity"): -100.0})), \
         patch("app.services.alpha_edge_engine.compute_options_hold_score", return_value=10), \
         patch("app.services.position_rotation.close_equity_trade", side_effect=fake_close_eq), \
         patch("app.services.position_rotation.close_options_trade", side_effect=fake_close_opt):
        await rotate_for_blocked_entry(incoming_ticker="TSLA", broker=MagicMock())

    # SPY (options, quality=10) ranks worse than AAPL (equity, quality=80) -> SPY closed.
    assert closed == ["SPY"]


@pytest.mark.asyncio
async def test_rotate_mixed_pool_closes_worse_ranked_equity_over_options(monkeypatch):
    from app.core import config as cfg
    monkeypatch.setattr(cfg.settings, "position_rotation_on_max", True)
    monkeypatch.setattr(cfg.settings, "position_rotation_closes", 1)

    trades = [
        _open_equity_row("t1", "AAPL", signal_score=0.9),
        _open_option_row("t2", "SPY", signal_score=0.9),
    ]
    session = _rotation_session(trades)
    closed: list[str] = []

    async def fake_close_eq(trade, **kwargs):
        closed.append(trade.underlying)
        return {"trade_id": str(trade.id), "ticker": trade.underlying, "status": "filled"}

    async def fake_close_opt(trade, **kwargs):
        closed.append(trade.underlying)
        return {"trade_id": str(trade.id), "ticker": trade.underlying, "status": "filled"}

    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.services.alpha_edge_engine.compute_equity_hold_score", return_value=5), \
         patch("app.services.position_rotation._unrealized_by_position",
               new=AsyncMock(return_value={("SPY", "options"): -50.0,
                                           ("AAPL", "equity"): -100.0})), \
         patch("app.services.alpha_edge_engine.compute_options_hold_score", return_value=90), \
         patch("app.services.position_rotation.close_equity_trade", side_effect=fake_close_eq), \
         patch("app.services.position_rotation.close_options_trade", side_effect=fake_close_opt):
        await rotate_for_blocked_entry(incoming_ticker="TSLA", broker=MagicMock())

    # AAPL (equity, quality=5) ranks worse than SPY (options, quality=90) -> AAPL closed.
    assert closed == ["AAPL"]


@pytest.mark.asyncio
async def test_rotate_triggers_for_incoming_options_signal_and_excludes_its_own_underlying(monkeypatch):
    """Trigger symmetry (5c): rotate_for_blocked_entry() doesn't take or
    check the incoming signal's asset type at all — it only excludes
    incoming_ticker's underlying from the candidate pool. This proves an
    incoming OPTIONS entry (e.g. a new SPY spread blocked by max_positions)
    triggers rotation exactly like an equity entry always has, and never
    rotates the very SPY options position it would conflict with."""
    from app.core import config as cfg
    monkeypatch.setattr(cfg.settings, "position_rotation_on_max", True)
    monkeypatch.setattr(cfg.settings, "position_rotation_closes", 1)

    trades = [
        _open_option_row("t1", "SPY", signal_score=0.9),   # same underlying as the incoming entry
        _open_equity_row("t2", "AAPL", signal_score=0.2),
    ]
    session = _rotation_session(trades)
    closed: list[str] = []

    async def fake_close_eq(trade, **kwargs):
        closed.append(trade.underlying)
        return {"trade_id": str(trade.id), "ticker": trade.underlying, "status": "filled"}

    async def fake_close_opt(trade, **kwargs):
        closed.append(trade.underlying)
        return {"trade_id": str(trade.id), "ticker": trade.underlying, "status": "filled"}

    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.services.alpha_edge_engine.compute_equity_hold_score", return_value=10), \
         patch("app.services.position_rotation._unrealized_by_position",
               new=AsyncMock(return_value={("SPY", "options"): -1000.0,
                                           ("AAPL", "equity"): -100.0})), \
         patch("app.services.alpha_edge_engine.compute_options_hold_score", return_value=0), \
         patch("app.services.position_rotation.close_equity_trade", side_effect=fake_close_eq), \
         patch("app.services.position_rotation.close_options_trade", side_effect=fake_close_opt):
        out = await rotate_for_blocked_entry(incoming_ticker="SPY", broker=MagicMock())

    # SPY would rank worst by every tier, but it IS the incoming underlying -> excluded.
    assert "SPY" not in closed
    assert closed == ["AAPL"]
    assert len(out) == 1


# ── Unknown P&L must never be treated as break-even ──────────────────────────
#
# _equity_unrealized() used to return 0.0 when the quote fetch failed or the
# entry price was missing, and the equity/options call sites defaulted to 0.0
# too. Winner Protection compares against a <= 0.0 floor, so a fabricated zero
# passed it — meaning a quote outage silently promoted every protected winner
# into a rotation candidate, exactly when the data was least trustworthy.
#
# Closing a position is irreversible and immediate; declining to rotate only
# leaves a signal blocked. The unknown case has to fall toward not acting.


def test_unknown_pnl_is_excluded_not_treated_as_break_even():
    # Two winners (protected) and one unknown. Under the old 0.0 default the
    # unknown would have been eligible; with count=1 it would have been closed.
    targets = select_rotation_targets(
        [_c("t1", "LITE", 1077.60), _c("t2", "MSTR", 19.36), _c("t3", "NBIS", None)],
        incoming_ticker="TSLA", count=1,
    )
    assert targets == []


def test_unknown_pnl_does_not_count_toward_the_required_candidate_count():
    """A real loser plus an unknown must not satisfy count=2 — otherwise an
    outage manufactures the second candidate needed to start rotating."""
    targets = select_rotation_targets(
        [_c("t1", "MRVL", -10586.85), _c("t2", "LITE", None)],
        incoming_ticker="TSLA", count=2,
    )
    assert targets == []


def test_a_real_loser_is_still_selected_alongside_an_unknown():
    """Fail-closed on the unknown must not disable rotation entirely."""
    targets = select_rotation_targets(
        [_c("t1", "MRVL", -10586.85), _c("t2", "LITE", None), _c("t3", "AMD", -400.0)],
        incoming_ticker="TSLA", count=2,
    )
    assert sorted(t.underlying for t in targets) == ["AMD", "MRVL"]


def test_a_genuine_zero_pnl_is_still_eligible():
    """None means unknown; 0.0 means a real flat position, which the floor
    admits. Conflating them would be the opposite over-correction."""
    targets = select_rotation_targets(
        [_c("t1", "AMD", 0.0), _c("t2", "INTC", -50.0)],
        incoming_ticker="TSLA", count=2,
    )
    assert sorted(t.underlying for t in targets) == ["AMD", "INTC"]


# ── _equity_unrealized returns None, never a fabricated 0.0 ──────────────────

def _pnl_trade(entry, qty=100, spread="equity_long"):
    return SimpleNamespace(credit_received=entry, quantity=qty, spread_type=spread)


def test_equity_unrealized_none_when_quote_is_missing():
    from app.services.position_rotation import _equity_unrealized
    assert _equity_unrealized(_pnl_trade(234.69), 0.0) is None


def test_equity_unrealized_none_when_entry_price_is_missing():
    from app.services.position_rotation import _equity_unrealized
    assert _equity_unrealized(_pnl_trade(None), 217.0) is None


def test_equity_unrealized_computes_a_real_loss():
    from app.services.position_rotation import _equity_unrealized
    got = _equity_unrealized(_pnl_trade(234.69, qty=599), 217.02)
    assert got is not None and round(got, 2) == round((217.02 - 234.69) * 599, 2)
