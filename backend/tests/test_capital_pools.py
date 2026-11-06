"""Per-asset-class capital pools: equity sized on $100k, options on $25k, one shared desk.

The owner's rule (2026-11): "if Equity it will start $100k and if Options, it will start
with $25k". Decision recorded with it: ONE shared desk on the combined $125k — loss limits,
drawdown and capital preservation are measured on the combined figure, and the two pools
grow and shrink together with the desk's P&L (each pool's share of the desk is fixed).

Pinned here: the startup refusal of a half-configured pair, the combined base, each
class's share, the sizing figure per class, that pools off is the old behaviour, and
that the call sites ask for the right class.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.services.account_state import capped_capital, get_account_value
from app.services.equity_signal_engine import compute_equity_trade_plan

EQ, OP = 100_000.0, 25_000.0
ANCHOR = 246_620.0


@contextmanager
def _pools(eq=EQ, op=OP, base=0.0, anchor=ANCHOR):
    with patch.object(settings, "trading_capital_base_equity", eq), \
         patch.object(settings, "trading_capital_base_options", op), \
         patch.object(settings, "trading_capital_base", base), \
         patch.object(settings, "trading_capital_anchor", anchor):
        yield


def _broker(net_liq):
    b = MagicMock()
    b.get_account_summary = AsyncMock(return_value=MagicMock(net_liquidation=net_liq))
    return b


# ── configuration ───────────────────────────────────────────────────────────

def test_a_half_configured_pair_is_refused_at_startup():
    for kw in ({"trading_capital_base_equity": 100_000.0}, {"trading_capital_base_options": 25_000.0}):
        with pytest.raises(ValidationError, match="must be set together"):
            Settings(**kw)


def test_both_or_neither_is_accepted():
    assert Settings().capital_pools_active is False
    s = Settings(trading_capital_base_equity=100_000.0, trading_capital_base_options=25_000.0)
    assert s.capital_pools_active is True


def test_a_negative_pool_is_refused():
    with pytest.raises(ValidationError):
        Settings(trading_capital_base_equity=-1.0, trading_capital_base_options=25_000.0)


def test_the_desk_base_is_the_pools_sum_and_overrides_the_single_base():
    s = Settings(trading_capital_base_equity=100_000.0, trading_capital_base_options=25_000.0,
                 trading_capital_base=999.0)
    assert s.effective_trading_capital_base == 125_000.0
    assert Settings(trading_capital_base=25_000.0).effective_trading_capital_base == 25_000.0
    assert Settings().effective_trading_capital_base == 0.0


@pytest.mark.parametrize("cls,share", [("equity", 0.8), ("options", 0.2), ("EQUITY", 0.8),
                                       ("crypto", 1.0), (None, 1.0)])
def test_each_class_has_its_share_and_unlisted_classes_get_the_whole_desk(cls, share):
    s = Settings(trading_capital_base_equity=100_000.0, trading_capital_base_options=25_000.0)
    assert s.capital_pool_share(cls) == pytest.approx(share)


def test_with_pools_off_every_share_is_one():
    s = Settings()
    assert [s.capital_pool_share(c) for c in ("equity", "options", None)] == [1.0, 1.0, 1.0]


# ── the figures sizing sees ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_at_the_anchor_each_class_sees_its_own_pool():
    with _pools(), patch("app.broker.broker_factory.get_broker", return_value=_broker(ANCHOR)):
        assert await get_account_value("equity") == pytest.approx(EQ)
        assert await get_account_value("options") == pytest.approx(OP)
        assert await get_account_value() == pytest.approx(EQ + OP)     # the whole desk


@pytest.mark.asyncio
async def test_a_desk_gain_or_loss_moves_both_pools_in_proportion():
    with _pools(), patch("app.broker.broker_factory.get_broker", return_value=_broker(ANCHOR - 12_500.0)):
        desk = 125_000.0 - 12_500.0                                    # -10% on the desk
        assert await get_account_value() == pytest.approx(desk)
        assert await get_account_value("equity") == pytest.approx(desk * 0.8)
        assert await get_account_value("options") == pytest.approx(desk * 0.2)


@pytest.mark.asyncio
async def test_the_pools_always_add_up_to_the_desk():
    with _pools(), patch("app.broker.broker_factory.get_broker", return_value=_broker(ANCHOR + 7_777.0)):
        total = await get_account_value("equity") + await get_account_value("options")
        assert total == pytest.approx(await get_account_value())


@pytest.mark.asyncio
async def test_pools_off_is_the_old_behaviour_for_every_class():
    with _pools(eq=0.0, op=0.0, base=25_000.0), \
         patch("app.broker.broker_factory.get_broker", return_value=_broker(ANCHOR)):
        assert await get_account_value("equity") == 25_000.0
        assert await get_account_value("options") == 25_000.0
        assert await get_account_value() == 25_000.0


@pytest.mark.asyncio
async def test_a_down_broker_falls_back_per_class_without_the_anchor_arithmetic():
    with _pools(), patch.object(settings, "starting_capital", 125_000.0), \
         patch("app.broker.broker_factory.get_broker", side_effect=ConnectionError("down")):
        assert await get_account_value("equity") == pytest.approx(EQ)
        assert await get_account_value("options") == pytest.approx(OP)


def test_a_failed_read_is_still_not_turned_into_a_figure():
    with _pools():
        assert capped_capital(0.0) == 0.0 and capped_capital(-5.0) == -5.0


# ── what the owner will actually see ────────────────────────────────────────

def test_amzn_sizes_off_the_equity_pool_not_the_options_one():
    """AMZN ~ $262.35: the 8% cap on $100k is 30 shares (it was 7 on a $25k desk)."""
    ind = {"close": 262.35, "atr": 5.0}
    with patch.object(settings, "equity_max_position_pct", 0.08):
        on_pool = compute_equity_trade_plan(ind, "BUY", portfolio_value=EQ)
        on_old_desk = compute_equity_trade_plan(ind, "BUY", portfolio_value=OP)
    assert on_pool["shares"] == 30 and on_old_desk["shares"] == 7


# ── the call sites ask for the right class ──────────────────────────────────

MAIN = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text()


def test_the_equity_scan_sizes_off_the_equity_pool():
    hits = re.findall(r'account_value\s*=\s*await get_account_value\("equity"\)', MAIN)
    assert len(hits) == 1, "the equity scan must ask for the equity pool"


def test_options_sizing_uses_the_options_pool_but_exposure_checks_use_the_whole_desk():
    assert re.search(r'sizing_capital\s*=\s*portfolio_value\s*\*\s*settings\.capital_pool_share\("options"\)', MAIN)
    assert re.search(r'calculate_position_size\(\s*portfolio_value=sizing_capital', MAIN)
    # the concentration/exposure state is still built on the whole desk figure
    assert re.search(r'_build_portfolio_risk_state\(portfolio_value\)', MAIN)
    assert re.search(r'portfolio_value\s*=\s*await get_account_value\(\)\s*\n\s*# What this trade is SIZED', MAIN)


# ── the shared desk's drawdown series and status route use the combined base ──

from test_trading_capital_anchor import _PeakDB, peakdb  # noqa: E402,F401  (fixture + recorder)


@pytest.mark.asyncio
async def test_the_desk_peak_is_seeded_at_the_combined_base(peakdb):
    """Seeded at 25k (the old single base) it would read a 125k desk as a +400% gain
    and then any normal day as a drawdown from that."""
    with _pools():
        peak = await peakdb._get_or_update_peak_value(EQ + OP - 1_000.0, 244_861.0)
    assert float(_PeakDB.added[0].peak_value) == EQ + OP and peak == EQ + OP
    assert _PeakDB.added[0].id == 2          # the desk's own row, as with a single base


@pytest.mark.asyncio
async def test_the_status_route_reports_the_combined_base_and_a_desk_loss_as_drawdown():
    import app.main as m
    from test_trading_capital_anchor import _FakeDB

    with _pools(), patch.object(settings, "starting_capital", EQ + OP), \
         patch("app.broker.broker_factory.get_broker", return_value=_broker(ANCHOR - 6_250.0)), \
         patch("app.core.database.AsyncSessionLocal", lambda: _FakeDB()), \
         patch.object(m, "_peak_value_cache", EQ + OP), \
         patch.object(m, "_maybe_log_guardrail_event", AsyncMock()):
        out = await m.guardrail_status()
    assert out["drawdown_pct"] == pytest.approx(6_250.0 / 125_000.0)      # 5% of the shared desk
    assert out["capital_pct_remaining"] == pytest.approx(0.95)
