"""/health reports whether the broker session is up, as true / false / null.

The gateway watchdog reads this. Two properties carry the weight:

* It must be a LOCAL read. /health is also the container healthcheck, so
  anything that can hang or fail turns a working backend "unhealthy".
* "Could not tell" must be null, never false. The watchdog restarts the gateway
  on false and does nothing on null; reporting an inability to check as
  "disconnected" is the mistake that caused ~800 needless restarts.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch


def _broker(connected=True, ib_connected=True, with_ib=True):
    b = MagicMock()
    b._connected = connected
    if with_ib:
        b.ib = MagicMock()
        b.ib.isConnected = MagicMock(return_value=ib_connected)
    else:
        del b.ib
    return b


def _health(broker):
    from app.main import health_check
    with patch("app.broker.broker_factory.get_broker", return_value=broker):
        return asyncio.run(health_check())


def test_connected_broker_reports_true():
    assert _health(_broker())["broker_connected"] is True


def test_either_flag_down_reports_false():
    assert _health(_broker(connected=False))["broker_connected"] is False
    assert _health(_broker(ib_connected=False))["broker_connected"] is False


def test_adapter_without_an_ib_socket_uses_its_own_flag():
    assert _health(_broker(connected=True, with_ib=False))["broker_connected"] is True
    assert _health(_broker(connected=False, with_ib=False))["broker_connected"] is False


def test_failing_to_find_out_is_null_not_false():
    from app.main import health_check
    with patch("app.broker.broker_factory.get_broker", side_effect=RuntimeError("no broker")):
        out = asyncio.run(health_check())
    assert out["broker_connected"] is None and out["status"] == "ok"
    b = _broker()
    b.ib.isConnected.side_effect = RuntimeError("socket gone")
    assert _health(b)["broker_connected"] is None


def test_existing_fields_are_unchanged_and_nothing_account_related_leaks():
    out = _health(_broker())
    assert out["status"] == "ok" and "broker" in out
    assert set(out) == {"status", "broker", "broker_connected"}   # one new bit, nothing else


def test_both_health_paths_serve_it_and_stay_public():
    from app.api.auth_deps import is_public_path
    import app.main as m
    paths = {getattr(r, "path", None) for r in m.app.routes}
    for p in ("/health", "/api/health"):
        assert p in paths and is_public_path(p)
