"""TRADING_CAPITAL_ANCHOR: the desk's capital moves with the desk's own P&L.

With only TRADING_CAPITAL_BASE set, capped_capital() was min(live, base): while the
account sits above the base the desk's capital is the base, always. A desk that lost
$2,000 still read as exactly 25,000, so the drawdown check (peak 25,000 vs current
25,000) and capital preservation (25,000 / 25,000) could never fire. The loss limits
still worked, because they sum closed-trade P&L from the database — which is why this
went unnoticed.

With the anchor (the account's net liquidation the day the base took effect) the
desk's capital is base + (live - anchor): the same account movement, unrealized
included, applied to the desk's base. The desk also gets its own persisted peak row
(id 2), since a 25k series cannot be compared with the stored 276k account peak.

Unset, every path is the old behaviour. A failed account read is never turned into a
figure, and a desk that has lost its entire base does not read as a failed read.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

import app.services.account_state as account_state
from app.core.config import Settings, settings
from app.services.account_state import DESK_CAPITAL_FLOOR, capped_capital, get_account_value
from app.services.guardrails import GuardrailEngine, PortfolioState

ANCHOR = 246_620.0
BASE = 25_000.0


@contextmanager
def _cfg(base=BASE, anchor=ANCHOR):
    with patch.object(settings, "trading_capital_base", base), \
         patch.object(settings, "trading_capital_anchor", anchor):
        yield


def _broker(net_liq):
    b = MagicMock()
    b.get_account_summary = AsyncMock(return_value=MagicMock(net_liquidation=net_liq))
    return b


# ── the helper ──────────────────────────────────────────────────────────────

def test_at_the_anchor_the_desk_has_exactly_its_base():
    with _cfg():
        assert capped_capital(ANCHOR) == BASE


def test_a_loss_on_the_account_lowers_the_desks_capital():
    with _cfg():
        assert capped_capital(ANCHOR - 2_000.0) == BASE - 2_000.0     # the point of the change


def test_a_gain_raises_it():
    with _cfg():
        assert capped_capital(ANCHOR + 3_000.0) == BASE + 3_000.0


def test_it_never_exceeds_the_live_account():
    with _cfg(anchor=10_000.0):                                        # an anchor below the base
        assert capped_capital(20_000.0) == 20_000.0


def test_losing_the_whole_base_is_floored_not_zero_or_negative():
    with _cfg():
        v = capped_capital(ANCHOR - BASE - 500.0)
    assert v == DESK_CAPITAL_FLOOR and v > 0          # <= 0 would read as a failed account read


@pytest.mark.parametrize("bad", [0.0, -5.0])
def test_a_failed_read_is_never_turned_into_a_figure(bad):
    with _cfg():
        assert capped_capital(bad) == bad


def test_an_anchor_without_a_base_changes_nothing():
    with _cfg(base=0.0, anchor=ANCHOR):
        assert capped_capital(250_000.0) == 250_000.0


def test_neither_set_is_the_old_behaviour():
    with _cfg(base=0.0, anchor=0.0):
        assert capped_capital(250_000.0) == 250_000.0


def test_a_base_without_an_anchor_keeps_the_older_ceiling(monkeypatch):
    monkeypatch.setattr(account_state, "_warned_no_anchor", True)
    with _cfg(anchor=0.0):
        assert capped_capital(ANCHOR) == BASE
        assert capped_capital(ANCHOR - 2_000.0) == BASE   # the hidden-loss behaviour, pinned
        assert capped_capital(18_000.0) == 18_000.0


def test_the_missing_anchor_is_warned_about_once(monkeypatch, caplog):
    monkeypatch.setattr(account_state, "_warned_no_anchor", False)
    with _cfg(anchor=0.0), caplog.at_level(logging.WARNING):
        capped_capital(ANCHOR)
        capped_capital(ANCHOR)
    hits = [r for r in caplog.records if "TRADING_CAPITAL_ANCHOR" in r.getMessage()]
    assert len(hits) == 1


def test_with_an_anchor_there_is_no_warning(monkeypatch, caplog):
    monkeypatch.setattr(account_state, "_warned_no_anchor", False)
    with _cfg(), caplog.at_level(logging.WARNING):
        capped_capital(ANCHOR)
    assert not [r for r in caplog.records if "TRADING_CAPITAL_ANCHOR" in r.getMessage()]


def test_a_negative_anchor_is_refused():
    with pytest.raises(ValidationError):
        Settings(trading_capital_anchor=-1.0)


def test_the_default_anchor_is_unset():
    assert Settings().trading_capital_anchor == 0.0


@pytest.mark.asyncio
async def test_get_account_value_follows_the_desks_pnl():
    with _cfg(), patch("app.broker.broker_factory.get_broker", return_value=_broker(ANCHOR - 1_500.0)):
        assert await get_account_value() == BASE - 1_500.0


@pytest.mark.asyncio
@pytest.mark.parametrize("starting,expected", [(244_861.0, BASE), (18_000.0, 18_000.0)])
async def test_a_down_broker_falls_back_to_starting_capital_bounded_by_the_base(starting, expected):
    """Not through the anchor arithmetic: 25,000 - 246,620 + 25,000 would floor at $1."""
    with _cfg(), patch.object(settings, "starting_capital", starting), \
         patch("app.broker.broker_factory.get_broker", side_effect=ConnectionError("down")):
        assert await get_account_value() == expected


@pytest.mark.asyncio
async def test_a_down_broker_with_no_base_is_unchanged():
    with _cfg(base=0.0, anchor=0.0), patch.object(settings, "starting_capital", 50_000.0), \
         patch("app.broker.broker_factory.get_broker", side_effect=ConnectionError("down")):
        assert await get_account_value() == 50_000.0


# ── the guardrails now see the desk's loss ──────────────────────────────────

def _state(current, peak, start=BASE):
    return PortfolioState(
        current_value=current, starting_capital=start, daily_pnl=Decimal("0"),
        weekly_pnl=Decimal("0"), monthly_pnl=Decimal("0"), consecutive_losses=0,
        trades_today=0, peak_value=peak,
    )


def test_a_desk_loss_now_reaches_the_drawdown_check():
    with _cfg():
        current = capped_capital(ANCHOR - 0.31 * BASE)                 # desk down 31%
    status = GuardrailEngine().check_all(_state(current, peak=BASE))
    assert status.drawdown_pct == pytest.approx(0.31)
    assert not status.trading_allowed


def test_the_same_loss_was_invisible_under_the_ceiling(monkeypatch):
    monkeypatch.setattr(account_state, "_warned_no_anchor", True)
    with _cfg(anchor=0.0):
        current = capped_capital(ANCHOR - 0.31 * BASE)
    status = GuardrailEngine().check_all(_state(current, peak=BASE))
    assert status.drawdown_pct == 0.0 and status.trading_allowed      # the flaw, pinned


def test_a_desk_loss_now_reaches_capital_preservation():
    with _cfg():
        current = capped_capital(ANCHOR - 0.40 * BASE)                 # desk at 60% of its base
    status = GuardrailEngine().check_all(_state(current, peak=current))
    assert status.capital_pct_remaining == pytest.approx(0.60)
    assert status.trading_mode == "capital_preservation"               # below the 0.65 threshold


# ── the peak: the desk's own series ─────────────────────────────────────────

class _PeakDB:
    """Records which risk_peak_state ids are read and written."""
    store: dict = {}
    got: list = []
    added: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, _model, row_id):
        _PeakDB.got.append(row_id)
        return _PeakDB.store.get(row_id)

    def add(self, row):
        _PeakDB.added.append(row)
        _PeakDB.store[row.id] = row

    async def commit(self):
        return None


@pytest.fixture
def peakdb():
    _PeakDB.store, _PeakDB.got, _PeakDB.added = {}, [], []
    import app.main as m
    with patch("app.core.database.AsyncSessionLocal", lambda: _PeakDB()), \
         patch.object(m, "_peak_value_cache", None):
        yield m


@pytest.mark.asyncio
async def test_the_desk_peak_uses_its_own_row_seeded_at_the_base_not_starting_capital(peakdb):
    # STARTING_CAPITAL is the account (244,861); seeding the desk row from it would
    # read a 25k desk as a 90% drawdown.
    with _cfg():
        peak = await peakdb._get_or_update_peak_value(BASE - 1_000.0, 244_861.0)
    assert _PeakDB.got == [2] and _PeakDB.added[0].id == 2
    assert float(_PeakDB.added[0].peak_value) == BASE
    assert peak == BASE


@pytest.mark.asyncio
async def test_the_desk_peak_ratchets_up_with_gains_and_is_not_clamped_to_the_base(peakdb):
    with _cfg():
        await peakdb._get_or_update_peak_value(BASE, BASE)
        peak = await peakdb._get_or_update_peak_value(BASE + 4_000.0, BASE)
    assert peak == BASE + 4_000.0 and peakdb._peak_value_cache == BASE + 4_000.0


@pytest.mark.asyncio
async def test_the_account_peak_row_is_untouched_when_the_desk_series_is_on(peakdb):
    _PeakDB.store[1] = MagicMock(id=1, peak_value=Decimal("276000"))
    with _cfg():
        await peakdb._get_or_update_peak_value(BASE, BASE)
    assert 1 not in _PeakDB.got and float(_PeakDB.store[1].peak_value) == 276_000.0


@pytest.mark.asyncio
async def test_without_the_anchor_the_original_row_and_clamp_are_used(peakdb, monkeypatch):
    monkeypatch.setattr(account_state, "_warned_no_anchor", True)
    _PeakDB.store[1] = MagicMock(id=1, peak_value=Decimal("276000"))
    with _cfg(anchor=0.0):
        peak = await peakdb._get_or_update_peak_value(BASE, BASE)
    assert _PeakDB.got == [1] and peak == BASE                         # clamped, as before


@pytest.mark.asyncio
async def test_with_neither_set_the_original_row_and_peak_are_used(peakdb):
    _PeakDB.store[1] = MagicMock(id=1, peak_value=Decimal("276000"))
    with _cfg(base=0.0, anchor=0.0):
        peak = await peakdb._get_or_update_peak_value(245_880.0, 244_861.0)
    assert _PeakDB.got == [1] and peak == 276_000.0


@pytest.mark.asyncio
async def test_a_failed_peak_write_falls_back_to_the_base_seed_without_caching(peakdb):
    class Boom:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *a):
            return False

    with _cfg(), patch("app.core.database.AsyncSessionLocal", lambda: Boom()):
        peak = await peakdb._get_or_update_peak_value(BASE - 500.0, 244_861.0)
    assert peak == BASE and peakdb._peak_value_cache is None


# ── end to end through the status route ─────────────────────────────────────

class _FakeDB:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *_a, **_k):
        r = MagicMock()
        r.scalar.return_value = 0
        r.scalars.return_value.all.return_value = []
        return r


@pytest.mark.asyncio
async def test_the_status_route_shows_a_desk_loss_as_drawdown():
    import app.main as m

    with _cfg(), patch.object(settings, "starting_capital", BASE), \
         patch("app.broker.broker_factory.get_broker", return_value=_broker(ANCHOR - 2_000.0)), \
         patch("app.core.database.AsyncSessionLocal", lambda: _FakeDB()), \
         patch.object(m, "_peak_value_cache", BASE), \
         patch.object(m, "_maybe_log_guardrail_event", AsyncMock()):
        out = await m.guardrail_status()
    assert out["drawdown_pct"] == pytest.approx(2_000.0 / BASE)       # 8%, not 0
    assert out["capital_pct_remaining"] == pytest.approx(0.92)
