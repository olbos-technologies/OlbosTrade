"""Tests for the KillSwitch engage / flatten / reset / rehydrate flows."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.broker.broker_interface import CancelSweep
from app.services.kill_switch import KillSwitch


def _db_session(row=None):
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: row))
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


def _positions():
    equity = NS(quantity=100, strike=Decimal("0"), underlying="AAPL", symbol="AAPL",
                expiration=date.today(), option_type="call")
    option = NS(quantity=-2, strike=Decimal("450"), underlying="SPY", symbol="SPY450P",
                expiration=date.today(), option_type="put")
    return [equity, option]


def _broker(*, sweep=None, residual_after_flatten=None):
    """A broker that cancels through the BROKER-NEUTRAL interface.

    The fixture used to stub `b.ib.openOrders`, which is what let the
    IBKR-only cancellation branch look tested while Alpaca was skipped
    entirely. Nothing here touches `.ib`.

    get_positions is a sequence: the first call is what the flatten loop sees,
    later calls are what reconciliation sees. By default the positions are
    gone the second time, modelling a flatten that worked;
    `residual_after_flatten` models one that did not.
    """
    b = MagicMock()
    b.cancel_all_open_orders = AsyncMock(
        return_value=sweep if sweep is not None
        else CancelSweep(requested=2, cancelled=2)
    )
    after = _positions() if residual_after_flatten is None else residual_after_flatten
    if residual_after_flatten is None:
        after = []
    b.get_positions = AsyncMock(side_effect=[_positions(), after, after, after])
    b.place_order = AsyncMock(return_value=MagicMock(status="filled", message=""))
    b.place_equity_order = AsyncMock(return_value=MagicMock(status="filled", message=""))
    return b


@pytest.mark.asyncio
async def test_engage_full_flow():
    ks = KillSwitch()
    sched = MagicMock()
    ks.configure(_broker(), sched)
    with patch("app.services.kill_switch.AsyncSessionLocal", return_value=_db_session()):
        res = await ks.engage("daily loss limit")
    assert ks.is_engaged
    assert res["scheduler_paused"] is True
    assert res["orders_cancelled"] == 2
    assert res["positions_flattened"] == 2
    assert res["db_persisted"] is True
    assert res["errors"] == []
    # Flatness is asserted from a re-read, not from the closing orders being
    # accepted.
    assert res["reconciliation"]["performed"] is True
    assert res["reconciliation"]["flat"] is True
    sched.pause.assert_called_once()


@pytest.mark.asyncio
async def test_engage_idempotent():
    ks = KillSwitch()
    ks.configure(_broker(), MagicMock())
    with patch("app.services.kill_switch.AsyncSessionLocal", return_value=_db_session()):
        await ks.engage("first")
        again = await ks.engage("second")
    assert again["status"] == "already_engaged"


@pytest.mark.asyncio
async def test_engage_without_broker_or_scheduler_records_errors():
    ks = KillSwitch()   # nothing configured
    with patch("app.services.kill_switch.AsyncSessionLocal", return_value=_db_session()):
        res = await ks.engage("manual")
    assert "scheduler_not_configured" in res["errors"]
    assert "broker_not_configured" in res["errors"]


@pytest.mark.asyncio
async def test_flatten_rejected_records_error():
    ks = KillSwitch()
    b = _broker()
    b.place_order = AsyncMock(return_value=MagicMock(status="rejected", message="no liquidity"))
    b.place_equity_order = AsyncMock(return_value=MagicMock(status="rejected", message="halted"))
    ks.configure(b, MagicMock())
    with patch("app.services.kill_switch.AsyncSessionLocal", return_value=_db_session()):
        res = await ks.engage("test")
    assert any("rejected" in e for e in res["errors"])
    assert res["positions_flattened"] == 0


@pytest.mark.asyncio
async def test_status_property():
    ks = KillSwitch()
    assert ks.status["engaged"] is False
    ks.configure(_broker(), MagicMock())
    with patch("app.services.kill_switch.AsyncSessionLocal", return_value=_db_session()):
        await ks.engage("x")
    s = ks.status
    assert s["engaged"] is True and s["reason"] == "x" and s["engaged_at"]


@pytest.mark.asyncio
async def test_reset_requires_authorization(monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings.kill_switch_reset_code", "TEST_RESET_CODE"
    )
    ks = KillSwitch()
    ks.configure(_broker(), MagicMock())
    with patch("app.services.kill_switch.AsyncSessionLocal", return_value=_db_session()):
        await ks.engage("x")
        bad = await ks.reset("wrong-code")
        assert bad["reset"] is False and ks.is_engaged
        good = await ks.reset("TEST_RESET_CODE")
    assert good["reset"] is True and not ks.is_engaged


@pytest.mark.asyncio
async def test_rehydrate_restores_engaged():
    ks = KillSwitch()
    row = MagicMock(event_type="kill_switch", timestamp=datetime.now(timezone.utc))
    with patch("app.services.kill_switch.AsyncSessionLocal", return_value=_db_session(row=row)):
        await ks.rehydrate()
    assert ks.is_engaged


@pytest.mark.asyncio
async def test_rehydrate_stays_disarmed_after_reset_event():
    ks = KillSwitch()
    row = MagicMock(event_type="kill_switch_reset", timestamp=datetime.now(timezone.utc))
    with patch("app.services.kill_switch.AsyncSessionLocal", return_value=_db_session(row=row)):
        await ks.rehydrate()
    assert not ks.is_engaged


@pytest.mark.asyncio
async def test_rehydrate_fails_closed_on_db_error(monkeypatch):
    """
    This test previously asserted `not ks.is_engaged` with the comment
    "defaults to not engaged" — it locked in the behaviour rehydrate()'s own
    docstring forbids: "if it was engaged when the process died/restarted, it
    MUST come back engaged, or a restart would silently re-enable trading."

    An unreadable state is not evidence of a clear switch. rehydrate() runs at
    start-up, where a DB that is not accepting connections yet is the ordinary
    case, so the old default re-enabled trading after a halt on nothing worse
    than a slow boot.
    """
    monkeypatch.setattr("app.services.kill_switch._REHYDRATE_BACKOFF_SECONDS", 0)
    ks = KillSwitch()
    with patch("app.services.kill_switch.AsyncSessionLocal", side_effect=Exception("down")):
        await ks.rehydrate()
    assert ks.is_engaged
    assert ks.status["rehydrate_unverified"] is True


@pytest.mark.asyncio
async def test_engage_resilient_when_every_step_fails():
    ks = KillSwitch()
    b = MagicMock()
    b.ib.openOrders.return_value = [MagicMock(orderId=1)]
    b.ib.cancelOrder.side_effect = Exception("cancel fail")
    zero = NS(quantity=0, strike=Decimal("0"), underlying="X", symbol="X",
              expiration=date.today(), option_type="call")
    eq = NS(quantity=10, strike=Decimal("0"), underlying="AAPL", symbol="AAPL",
            expiration=date.today(), option_type="call")
    opt = NS(quantity=-1, strike=Decimal("450"), underlying="SPY", symbol="SPY450P",
             expiration=date.today(), option_type="put")
    b.get_positions = AsyncMock(return_value=[zero, eq, opt])
    b.place_order = AsyncMock(side_effect=Exception("opt fail"))
    b.place_equity_order = AsyncMock(side_effect=Exception("eq fail"))
    sched = MagicMock()
    sched.pause.side_effect = Exception("sched fail")
    ks.configure(b, sched)

    session = _db_session()
    session.commit = AsyncMock(side_effect=Exception("persist fail"))
    with patch("app.services.kill_switch.AsyncSessionLocal", return_value=session):
        res = await ks.engage("test")

    assert ks.is_engaged
    assert res["positions_flattened"] == 0          # zero-qty skipped; others raised
    assert res["db_persisted"] is False
    assert any("scheduler_pause" in e for e in res["errors"])
    assert any("cancel_order" in e for e in res["errors"])
    assert any("flatten" in e for e in res["errors"])
    assert any("db_persist" in e for e in res["errors"])


@pytest.mark.asyncio
async def test_engage_broker_without_ib_skips_cancel():
    ks = KillSwitch()
    b = NS(
        get_positions=AsyncMock(return_value=[]),
        place_order=AsyncMock(return_value=MagicMock(status="filled", message="")),
        place_equity_order=AsyncMock(return_value=MagicMock(status="filled", message="")),
    )
    ks.configure(b, MagicMock())
    with patch("app.services.kill_switch.AsyncSessionLocal", return_value=_db_session()):
        res = await ks.engage("no-ib")
    assert res["orders_cancelled"] == 0   # no ib → cancel branch skipped


@pytest.mark.asyncio
async def test_engage_get_positions_error():
    ks = KillSwitch()
    b = MagicMock()
    b.ib.openOrders.return_value = []
    b.get_positions = AsyncMock(side_effect=Exception("positions down"))
    ks.configure(b, MagicMock())
    with patch("app.services.kill_switch.AsyncSessionLocal", return_value=_db_session()):
        res = await ks.engage("test")
    assert any("get_positions" in e for e in res["errors"])


@pytest.mark.asyncio
async def test_reset_without_scheduler(monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings.kill_switch_reset_code", "TEST_RESET_CODE"
    )
    ks = KillSwitch()
    ks.configure(_broker())   # no scheduler
    with patch("app.services.kill_switch.AsyncSessionLocal", return_value=_db_session()):
        await ks.engage("x")
        out = await ks.reset("TEST_RESET_CODE")
    assert out["reset"] is True and not ks.is_engaged
