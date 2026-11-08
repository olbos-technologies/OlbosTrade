"""The capacity panel's numbers must be the gate's numbers.

Written for the question "the limit is 5, why is it trading 3?". The panel shows
the arithmetic, so it is only worth having if it is the SAME arithmetic the
portfolio gate enforces; effective_max_positions() is that single source, and the
first test pins it to the formula the gate used before it was extracted.

Also pinned: an unreadable open count is null (never 0), blocked entries are
grouped by reason, and the route needs a session.
"""
from __future__ import annotations

import math
from unittest.mock import AsyncMock, patch

import pytest

from app.api.routes import desk_capacity as route
from app.services import desk_capacity as cap
from app.services.execution_portfolio_gate import (
    REGIME_CONCURRENCY_MULTIPLIER, effective_max_positions,
)


def _old_gate_formula(global_cap, mode_cap, regime):
    """The arithmetic as it stood inline in evaluate_portfolio_gates."""
    max_pos = min(global_cap, mode_cap)
    m = REGIME_CONCURRENCY_MULTIPLIER.get(regime, 1.0)
    tight = m < 1.0
    if tight:
        max_pos = max(1, math.floor(max_pos * m))
    return max_pos, m, tight


@pytest.mark.parametrize("regime", [*REGIME_CONCURRENCY_MULTIPLIER, "unrecognised", None])
@pytest.mark.parametrize("global_cap", [1, 2, 3, 5, 8])
@pytest.mark.parametrize("mode_cap", [1, 2, 3, 6, 10])
def test_the_shared_rule_matches_the_gates_original_arithmetic(global_cap, mode_cap, regime):
    assert effective_max_positions(global_cap, mode_cap, regime) == _old_gate_formula(global_cap, mode_cap, regime)


def test_the_table_it_reads_is_the_gates_and_includes_the_tightening_regimes():
    assert len(REGIME_CONCURRENCY_MULTIPLIER) >= 4
    assert effective_max_positions(5, 5, "high_vol_trending")[0] == 3      # floor(3.75)
    assert effective_max_positions(5, 5, "unknown")[0] == 2                # floor(2.5)
    assert effective_max_positions(5, 5, "crisis")[0] == 1                 # never below 1
    assert effective_max_positions(5, 6, "normal_mean_revert")[0] == 5


# ── describe ───────────────────────────────────────────────────────────────

def _d(**kw):
    base = dict(global_cap=5, style="aggressive", style_cap=6, regime="high_vol_trending",
                open_positions=3, blocks=[])
    base.update(kw)
    return cap.describe_capacity(**base)


def test_it_spells_out_why_five_becomes_three():
    d = _d()
    assert (d["global_cap"], d["style"]["cap"], d["base_cap"], d["effective_cap"]) == (5, 6, 5, 3)
    assert d["limited_by"] == "global" and d["regime"]["tightened"] is True
    assert d["room"] == 0
    assert any("high_vol_trending" in s and "5 → 3" in s for s in d["steps"])


def test_no_regime_step_when_the_regime_does_not_tighten():
    d = _d(regime="normal_mean_revert", open_positions=3)
    assert d["effective_cap"] == 5 and d["room"] == 2
    assert not any("regime" in s.lower() for s in d["steps"])


def test_the_style_can_be_the_limit():
    d = _d(global_cap=5, style="scalper", style_cap=2, regime="normal_mean_revert")
    assert (d["base_cap"], d["limited_by"], d["effective_cap"]) == (2, "style", 2)


def test_an_unclassified_market_is_reported_as_such_not_invented():
    for regime in (None, "unknown"):
        d = _d(regime=regime)
        assert d["regime"]["known"] is False and d["regime"]["name"] == "unknown"
        assert d["effective_cap"] == 2          # unknown tightens to 0.5


def test_an_unreadable_open_count_stays_unknown_not_zero():
    d = _d(open_positions=None)
    assert d["open_positions"] is None and d["room"] is None


def test_room_is_never_negative():
    assert _d(open_positions=9)["room"] == 0


# ── blocked-entry reasons ──────────────────────────────────────────────────

def test_blocks_are_grouped_by_reason_and_ranked():
    rows = [
        ("blocked", {"reason": "Max concurrent positions reached: 3/3 (regime-tightened: high_vol_trending)"}),
        ("blocked", {"reason": "Max concurrent positions reached: 3/3"}),
        ("skipped", {"reason": "duplicate_open: AMZN"}),
        ("blocked", {"reason": "Sector concentration: tech (NVDA) would be 31.0% of portfolio"}),
        ("submitted", {"reason": "ignored: not a block"}),
        ("blocked", {}),
        ("blocked", None),
    ]
    out = cap.top_block_reasons(rows)
    assert out[0] == {"reason": "Max concurrent positions reached", "count": 2}
    assert {"reason": "duplicate_open", "count": 1} in out
    assert {"reason": "no reason recorded", "count": 2} in out
    assert all(r["reason"] != "ignored" for r in out)


def test_only_the_top_reasons_are_returned():
    rows = [("blocked", {"reason": f"r{i}: x"}) for i in range(12)]
    assert len(cap.top_block_reasons(rows)) == 5


# ── the route ──────────────────────────────────────────────────────────────

class _Cfg:
    class mode:
        value = "aggressive"
    max_concurrent = 6


def _run(state=None, state_exc=None, rows=None, rows_exc=None, regime="high_vol_trending"):
    from types import SimpleNamespace
    st = AsyncMock(return_value=SimpleNamespace(open_position_count=state), side_effect=state_exc)
    rw = AsyncMock(return_value=rows or [], side_effect=rows_exc)
    with patch("app.services.execution_portfolio_gate.load_portfolio_risk_state", new=st), \
         patch.object(route, "_recent_block_rows", new=rw), \
         patch("app.main._regime_name", return_value=regime), \
         patch("app.services.trading_mode.trading_mode_manager") as tm:
        tm.config = _Cfg
        import asyncio
        return asyncio.run(route.capacity())


def test_the_route_reports_the_live_rule_and_count():
    out = _run(state=3, rows=[("blocked", {"reason": "Max concurrent positions reached: 3/3"})])
    assert out["effective_cap"] == 3 and out["open_positions"] == 3 and out["room"] == 0
    assert out["recent_blocks"][0]["count"] == 1 and out["blocks_available"] is True


def test_a_broker_or_db_failure_gives_null_not_zero():
    out = _run(state_exc=RuntimeError("gateway down"))
    assert out["open_positions"] is None and out["room"] is None
    assert out["effective_cap"] == 3          # the rule itself is still known


def test_a_failed_block_read_is_flagged_not_shown_as_no_blocks():
    out = _run(state=1, rows_exc=RuntimeError("db"))
    assert out["blocks_available"] is False and out["recent_blocks"] == []


def test_the_route_is_registered_and_needs_a_session():
    from app.api.auth_deps import is_public_path
    from app.main import app

    assert is_public_path("/api/health") is True, "the check is not reading the real allowlist"
    assert is_public_path("/api/trade-desk/capacity") is False
    assert "/api/trade-desk/capacity" in {r.path for r in app.routes}
