"""
Pre-rotation checklist and dry-run.

The two properties worth pinning: unknown must block just as hard as fail
(the cost of a wrong "proceed" is a real position closed), and the dry-run
must refuse to rehearse cleanly against a dirty preflight — a green dry-run
over 20 orphaned orders is exactly the false assurance the checklist exists
to prevent.

Run with: pytest tests/test_rotation_preflight.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import rotation_preflight as pf

from conftest import single_operator_conn

# Batch E2 gave these routes a `conn` so they can resolve a trade scope; with
# auth disabled it is never read. See conftest.single_operator_conn.
_CONN = single_operator_conn()



def _order(order_id=1, symbol="MRVL", protective=True, parent=None,
           action="SELL", otype="STP", remaining=599.0, stop=198.0, limit=None):
    return {"order_id": order_id, "symbol": symbol, "is_protective": protective,
            "parent_id": parent, "action": action, "order_type": otype,
            "remaining": remaining, "stop_price": stop, "limit_price": limit,
            "filled": 0.0, "status": "PreSubmitted"}


# ── Individual checks ────────────────────────────────────────────────────────

def test_orphan_check_fails_on_an_order_with_no_position():
    out = pf._no_orphans([_order(1, "MRVL"), _order(2, "EXC")], held={"MRVL"})
    assert out["status"] == pf.FAIL
    assert out["orphans"] == {"EXC": 1}


def test_orphan_check_passes_when_every_order_maps_to_a_position():
    out = pf._no_orphans([_order(1, "MRVL")], held={"MRVL"})
    assert out["status"] == pf.PASS


def test_duplicate_check_catches_an_identical_pair():
    out = pf._no_duplicates([_order(1), _order(2)])   # same symbol/side/type/qty/price
    assert out["status"] == pf.FAIL


def test_duplicate_check_allows_distinct_tranches():
    out = pf._no_duplicates([_order(1, remaining=85.0), _order(2, remaining=86.0)])
    assert out["status"] == pf.PASS


def test_bracket_check_fails_on_an_unparented_stop_with_no_position():
    """The exact production shape: a protective stop that lost its parent and
    guards nothing. If touched it opens a position, and no sibling cancels."""
    out = pf._brackets_consistent([_order(1, "EXC", protective=True, parent=None)],
                                  held={"MRVL"})
    assert out["status"] == pf.FAIL
    assert out["order_ids"] == [1]


def test_bracket_check_reports_lost_parent_links_on_held_positions_without_blocking():
    out = pf._brackets_consistent([_order(1, "MRVL", parent=None)], held={"MRVL"})
    assert out["status"] == pf.PASS
    assert out["lost_parent_link"] == [1]


def test_order_book_from_cache_is_not_trusted():
    out = pf._orders_synchronized({"source": "cache_after_refresh_failed", "orders": []})
    assert out["status"] == pf.FAIL


def test_winner_protection_check_is_behavioural():
    """Asserts against the real ranking code, not a config value."""
    out = pf._winner_protection()
    assert out["status"] == pf.PASS


@pytest.mark.asyncio
async def test_approval_boundary_check_is_behavioural_and_sends_nothing():
    out = await pf._approval_boundary()
    assert out["status"] == pf.PASS
    assert "refused before any broker call" in out["detail"]


@pytest.mark.asyncio
async def test_account_state_fails_when_pushes_are_stale():
    """The subscribe wedge must surface here even though cached reads work."""
    broker = MagicMock()
    broker.get_account_summary = AsyncMock(return_value=SimpleNamespace(
        data_age_seconds=1200.0, is_stale=False))
    out = await pf._account_state(broker)
    assert out["status"] == pf.FAIL
    assert "served from cache" in out["detail"]


@pytest.mark.asyncio
async def test_account_state_passes_on_fresh_pushes():
    broker = MagicMock()
    broker.get_account_summary = AsyncMock(return_value=SimpleNamespace(
        data_age_seconds=12.0, is_stale=False))
    out = await pf._account_state(broker)
    assert out["status"] == pf.PASS


# ── Aggregation: unknown blocks as hard as fail ──────────────────────────────

@pytest.mark.asyncio
async def test_unknown_blocks_all_clear_not_just_fail():
    broker = MagicMock()
    broker.ib = MagicMock()
    broker.ib.isConnected.return_value = True
    broker._connected = True
    # Everything else raises → UNKNOWN, never PASS.
    broker.get_account_summary = AsyncMock(side_effect=ConnectionError("no"))
    broker.get_positions = AsyncMock(side_effect=ConnectionError("no"))
    broker.get_open_orders = AsyncMock(side_effect=ConnectionError("no"))

    out = await pf.run_preflight(broker)
    assert out["all_clear"] is False
    assert out["unknown"], "unknown checks must be reported, not silently passed"


# ── Dry-run gating ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dry_run_refuses_to_rehearse_against_a_dirty_preflight():
    from app.api.routes.rotation import DryRunRequest, rotation_dry_run
    with patch("app.broker.broker_factory.get_broker", return_value=MagicMock()), \
         patch("app.services.rotation_preflight.run_preflight",
               new=AsyncMock(return_value={
                   "all_clear": False, "passed": 11, "total": 13,
                   "failed": ["no_orphan_orders"], "unknown": [], "checks": []})), \
         patch("app.services.position_rotation.propose_rotation_incumbent",
               new=AsyncMock()) as detect:
        out = await rotation_dry_run(DryRunRequest(ticker="TSLA"), _CONN)

    assert out["status"] == "blocked_by_preflight"
    assert out["submitted_anything"] is False
    detect.assert_not_called()   # did not even proceed to Detect


@pytest.mark.asyncio
async def test_dry_run_never_submits_even_when_preflight_is_clear():
    from app.api.routes.rotation import DryRunRequest, rotation_dry_run
    broker = MagicMock()
    broker.place_equity_order = AsyncMock()
    broker.place_order = AsyncMock()
    broker.cancel_open_orders = AsyncMock()
    broker.get_equity_positions = AsyncMock(
        return_value=[SimpleNamespace(symbol="MRVL", quantity=599)])

    incumbent = SimpleNamespace(trade_id="t1", underlying="MRVL",
                                spread_type="equity_long", quality_score=41.0,
                                confidence=0.62, in_flagged_cluster=False,
                                unrealized_pnl=-11239.22)
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    res = MagicMock(); res.scalars.return_value = MagicMock(first=lambda: object())
    session.execute = AsyncMock(return_value=res)

    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.services.rotation_preflight.run_preflight",
               new=AsyncMock(return_value={"all_clear": True, "passed": 13,
                                           "total": 13, "failed": [], "unknown": [],
                                           "checks": []})), \
         patch("app.services.position_rotation.propose_rotation_incumbent",
               new=AsyncMock(return_value=incumbent)), \
         patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False):
        out = await rotation_dry_run(DryRunRequest(
            ticker="TSLA", action="BUY", confidence=0.95, alpha_edge_score=88,
            entry_price=100.0, stop_price=96.0, target_price=108.0, shares=10), _CONN)

    assert out["status"] == "dry_run_complete"
    assert out["submitted_anything"] is False
    broker.place_equity_order.assert_not_called()
    broker.place_order.assert_not_called()
    broker.cancel_open_orders.assert_not_called()

    stages = {s["stage"]: s for s in out["stages"]}
    assert set(stages) == {"preflight", "detect", "compare", "rotation_review",
                           "approval", "risk_validation", "execution_intent"}
    # Approval must never be "ok" in a dry run — no token is minted.
    assert stages["approval"]["ok"] is False
    # And the intent must describe a real close sized from the BROKER.
    intent = stages["execution_intent"]["close_incumbent"]
    assert intent["symbol"] == "MRVL" and intent["side"] == "SELL"
    assert intent["quantity"] == 599.0
    assert "broker live position" in intent["source"]


@pytest.mark.asyncio
async def test_risk_engine_check_calls_the_real_engine():
    """Pins the call shape. The first version imported a module-level
    `guardrails` singleton that does not exist — GuardrailEngine is a class,
    and check_all takes the portfolio state rather than fetching it. That
    surfaced only in production as an UNKNOWN, which correctly blocked
    all_clear but told us nothing useful."""
    from app.services.guardrails import PortfolioState

    clean = PortfolioState(current_value=250_000.0, starting_capital=250_000.0,
                           daily_pnl=0.0, weekly_pnl=0.0, monthly_pnl=0.0,
                           consecutive_losses=0, trades_today=0)
    with patch("app.api.routes.trade_desk._fetch_portfolio_state",
               new=AsyncMock(return_value=clean)):
        out = await pf._risk_engine()

    assert out["status"] in (pf.PASS, pf.FAIL), out["detail"]
    assert "trading_allowed" in out["detail"]


# ── The two constraint inputs that were never connected ──────────────────────
#
# Found by running the dry-run rather than trusting it: every review returned
# "hold" on challenger_liquidity_unknown + portfolio_heat_unknown, so rotation
# could never be recommended even when it should be. The machinery was right;
# two inputs were not wired.

def test_liquidity_reads_volume_ratio_from_the_indicators_block():
    """It lives at signal["indicators"]["volume_ratio"], not top level. The
    first wiring read the top level and so was always None."""
    from app.api.routes.trade_desk import _challenger_liquidity_ok

    assert _challenger_liquidity_ok({"indicators": {"volume_ratio": 1.2}}) is True
    assert _challenger_liquidity_ok({"indicators": {"volume_ratio": 0.2}}) is False
    # Top-level only — the old shape — must still read as unknown, not True.
    assert _challenger_liquidity_ok({"volume_ratio": 1.2}) is None


def test_liquidity_unknown_when_the_indicator_is_absent_or_junk():
    from app.api.routes.trade_desk import _challenger_liquidity_ok

    assert _challenger_liquidity_ok({}) is None
    assert _challenger_liquidity_ok({"indicators": {}}) is None
    assert _challenger_liquidity_ok({"indicators": {"volume_ratio": None}}) is None
    assert _challenger_liquidity_ok({"indicators": {"volume_ratio": "n/a"}}) is None


def test_heat_threshold_compares_fractions_not_percents():
    """compute_portfolio_risk returns heat * 100. Comparing that percent
    against the 0.35 fraction vetoed any book with over 0.35% heat — i.e.
    essentially always."""
    from app.services.rotation_review import (
        MAX_PORTFOLIO_HEAT_FRACTION, PositionFacts, build_rotation_review,
    )

    inc = PositionFacts(ticker="MRVL", side="incumbent", quality_score=20.0)
    chal = PositionFacts(ticker="TSLA", side="challenger", quality_score=90.0,
                         liquidity_ok=True, in_flagged_cluster=False)

    # 12% heat as a fraction is comfortably under the limit.
    ok = build_rotation_review(incumbent=inc, challenger=chal,
                               portfolio_heat_fraction=0.12)
    assert ok["hard_constraint_failures"] == []
    assert ok["recommendation"] == "replace"

    # Over the limit still vetoes.
    hot = build_rotation_review(incumbent=inc, challenger=chal,
                                portfolio_heat_fraction=MAX_PORTFOLIO_HEAT_FRACTION + 0.01)
    assert hot["recommendation"] == "hold"
    assert any("portfolio_heat" in f for f in hot["hard_constraint_failures"])


@pytest.mark.asyncio
async def test_heat_is_none_rather_than_computed_off_a_guessed_capital_base():
    """A zero/unknown account value must yield None — an unverifiable
    constraint blocks, it does not get a made-up denominator."""
    from app.api.routes.trade_desk import _portfolio_heat_fraction
    assert await _portfolio_heat_fraction(0.0) is None
    assert await _portfolio_heat_fraction(-1.0) is None


# ── Heat must measure risk-at-stake, not deployment ──────────────────────────
#
# portfolio_engine defines equity risk_dollars as entry x shares — full
# notional. On 2026-08-29 that made portfolio_heat_pct read 94.06% ($220,715
# notional / $234,651 capital), accurate as "how invested" and meaningless as
# "how much at risk". Gated at 35% it vetoed every rotation.

@pytest.mark.asyncio
async def test_heat_uses_stop_distance_not_notional():
    from app.api.routes import trade_desk as td

    trade = SimpleNamespace(underlying="MRVL", credit_received=234.69,
                            quantity=599, status="open")
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    res = MagicMock(); res.scalars.return_value = MagicMock(all=lambda: [trade])
    session.execute = AsyncMock(return_value=res)

    broker = MagicMock()
    broker.get_open_orders = AsyncMock(return_value={
        "source": "refreshed",
        "orders": [{"symbol": "MRVL", "is_protective": True, "remaining": 599.0,
                    "stop_price": 198.0}],
    })

    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.broker.broker_factory.get_broker", return_value=broker):
        heat = await td._portfolio_heat_fraction(234651.24)

    # Risk-at-stake: |234.69 - 198.00| * 599 = 21,977.31 -> ~9.4% of capital.
    # The notional reading of the same position would be ~60%.
    assert heat is not None
    assert 0.09 < heat < 0.10, heat


@pytest.mark.asyncio
async def test_heat_is_none_when_a_position_has_no_protective_stop():
    """An unmeasurable position must make the whole reading None. A partial
    sum would understate heat, and understating a safety check's input is the
    wrong direction to be wrong in."""
    from app.api.routes import trade_desk as td

    trades = [SimpleNamespace(underlying="MRVL", credit_received=234.69,
                              quantity=599, status="open"),
              SimpleNamespace(underlying="NAKED", credit_received=100.0,
                              quantity=10, status="open")]
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    res = MagicMock(); res.scalars.return_value = MagicMock(all=lambda: trades)
    session.execute = AsyncMock(return_value=res)

    broker = MagicMock()
    broker.get_open_orders = AsyncMock(return_value={
        "source": "refreshed",
        "orders": [{"symbol": "MRVL", "is_protective": True, "remaining": 599.0,
                    "stop_price": 198.0}],
    })

    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.broker.broker_factory.get_broker", return_value=broker):
        assert await td._portfolio_heat_fraction(234651.24) is None


@pytest.mark.asyncio
async def test_heat_is_none_on_a_cache_fallback_order_book():
    """A cache fall-back cannot distinguish 'no stop' from 'stop unseen'."""
    from app.api.routes import trade_desk as td

    trade = SimpleNamespace(underlying="MRVL", credit_received=234.69,
                            quantity=599, status="open")
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    res = MagicMock(); res.scalars.return_value = MagicMock(all=lambda: [trade])
    session.execute = AsyncMock(return_value=res)

    broker = MagicMock()
    broker.get_open_orders = AsyncMock(
        return_value={"source": "cache_after_refresh_failed", "orders": []})

    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.broker.broker_factory.get_broker", return_value=broker):
        assert await td._portfolio_heat_fraction(234651.24) is None


@pytest.mark.asyncio
async def test_heat_is_zero_with_no_open_positions():
    from app.api.routes import trade_desk as td
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    res = MagicMock(); res.scalars.return_value = MagicMock(all=lambda: [])
    session.execute = AsyncMock(return_value=res)
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        assert await td._portfolio_heat_fraction(234651.24) == 0.0


def test_margin_sign_is_rendered_by_the_formatter_not_hardcoded():
    """The reason string used to hardcode a '+' in front of a signed value, so
    a challenger scoring below the incumbent read '= +-11.6'. That is the
    common case and the one an operator most needs to read cleanly."""
    from app.services.rotation_review import PositionFacts, build_rotation_review

    def review(inc_q, chal_q):
        return build_rotation_review(
            incumbent=PositionFacts(ticker="MSTR", side="incumbent", quality_score=inc_q),
            challenger=PositionFacts(ticker="SBUX", side="challenger", quality_score=chal_q,
                                     liquidity_ok=True, in_flagged_cluster=False),
            portfolio_heat_fraction=0.13)

    worse = " ".join(review(84.0, 72.44)["reasons"])
    assert "+-" not in worse, worse
    assert "-11.6" in worse

    better = " ".join(review(50.0, 90.0)["reasons"])
    assert "+40.0" in better, better
