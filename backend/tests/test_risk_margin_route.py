"""Tests for the GET /api/risk/margin endpoint."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes.risk import get_margin

from conftest import single_operator_conn


def _broker(**kw):
    # Mirrors AccountSummary's real shape, age fields included: the route
    # reports how old the figures are, and a stand-in missing those fields
    # would pass tests the real model would fail.
    summary = SimpleNamespace(
        net_liquidation=Decimal(str(kw.get("nl", 100_000))),
        maintenance_margin=kw.get("mm", Decimal("10000")),
        excess_liquidity=kw.get("el", Decimal("90000")),
        buying_power=kw.get("bp", Decimal("200000")),
        init_margin=kw.get("im", Decimal("12000")),
        data_age_seconds=kw.get("age", 12.0),
        is_stale=kw.get("stale", False),
    )
    b = MagicMock()
    b.get_account_summary = AsyncMock(return_value=summary)
    return b


@pytest.mark.asyncio
async def test_margin_route_ok():
    with patch("app.broker.broker_factory.get_broker", return_value=_broker()):
        res = await get_margin()
    assert res["available"] is True and res["status"] == "ok"
    assert res["utilization_pct"] == 10.0


@pytest.mark.asyncio
async def test_margin_route_critical():
    with patch("app.broker.broker_factory.get_broker",
               return_value=_broker(mm=Decimal("85000"), el=Decimal("15000"))):
        res = await get_margin()
    assert res["available"] is True and res["status"] == "critical"


@pytest.mark.asyncio
async def test_margin_route_unavailable_when_not_reported():
    with patch("app.broker.broker_factory.get_broker",
               return_value=_broker(mm=None)):
        res = await get_margin()
    assert res["available"] is False and "did not report" in res["reason"]


@pytest.mark.asyncio
async def test_margin_route_broker_error():
    b = MagicMock()
    b.get_account_summary = AsyncMock(side_effect=Exception("disconnected"))
    with patch("app.broker.broker_factory.get_broker", return_value=b):
        res = await get_margin()
    assert res["available"] is False and "disconnected" in res["reason"]


# ── /api/risk/reconciliation ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_reconciliation_route_reports_status():
    from types import SimpleNamespace
    from datetime import datetime, timezone
    from app.api.routes.risk import get_reconciliation

    fake = SimpleNamespace(
        clean=False, broker_position_count=1, db_open_trade_count=0,
        untracked_at_broker=["SPY"], phantom_in_db=[], warnings=[],
        checked_at=datetime.now(timezone.utc),
    )
    recon = MagicMock()
    recon.check = AsyncMock(return_value=fake)
    from app.services.worker_scope import ScopedWork

    with patch("app.broker.broker_factory.get_broker", return_value=MagicMock()), \
         patch("app.services.worker_scope.broker_for_scope",
               new=AsyncMock(return_value=ScopedWork(
                   organization_id=None, broker=MagicMock(),
                   broker_source="process"))), \
         patch("app.services.position_reconciler.PositionReconciler", return_value=recon):
        res = await get_reconciliation(single_operator_conn())
    assert res["clean"] is False and res["untracked_at_broker"] == ["SPY"]
    assert res["broker_position_count"] == 1 and "checked_at" in res


# ── /api/risk/kill-switch/engage (operator-facing) ─────────────────────────────
@pytest.mark.asyncio
async def test_engage_ui_engages_when_idle():
    from app.api.routes import risk as risk_mod
    ks = MagicMock()
    ks.is_engaged = False
    ks.engage = AsyncMock(return_value={"errors": []})
    with patch.object(risk_mod, "kill_switch_service", ks):
        res = await risk_mod.engage_kill_switch_ui()
    assert res["status"] == "engaged"
    ks.engage.assert_awaited_once()


@pytest.mark.asyncio
async def test_engage_ui_idempotent_when_already_engaged():
    from app.api.routes import risk as risk_mod
    ks = MagicMock()
    ks.is_engaged = True
    ks.status = {"engaged": True}
    ks.engage = AsyncMock()
    with patch.object(risk_mod, "kill_switch_service", ks):
        res = await risk_mod.engage_kill_switch_ui()
    assert res["status"] == "already_engaged"
    ks.engage.assert_not_awaited()


@pytest.mark.asyncio
async def test_margin_route_reports_data_age():
    """Utilization reads as a live measurement whether or not it still is one,
    so the age has to travel with it."""
    with patch("app.broker.broker_factory.get_broker",
               return_value=_broker(age=45.0, stale=False)):
        res = await get_margin()
    assert res["data_age_seconds"] == 45.0
    assert res["is_stale"] is False


@pytest.mark.asyncio
async def test_margin_route_flags_stale_figures():
    with patch("app.broker.broker_factory.get_broker",
               return_value=_broker(age=3600.0, stale=True)):
        res = await get_margin()
    # Still served — the operator needs the last-known picture — but labelled,
    # so a 57% utilization from an hour ago can't pass for a current one.
    assert res["available"] is True
    assert res["is_stale"] is True
    assert res["data_age_seconds"] == 3600.0
