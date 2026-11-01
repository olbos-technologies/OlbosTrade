"""
Tests for GET /api/market/broker — connectivity/paper-mode detection must
work correctly per-broker, not just for IBKR's socket-based connection model.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.api.routes.market_data import get_broker_status


@pytest.mark.asyncio
async def test_ibkr_connected_when_socket_and_flag_both_true():
    broker = MagicMock()
    broker.supports_options = True
    broker.supports_equities = True
    broker._connected = True
    broker.ib = MagicMock()
    broker.ib.isConnected.return_value = True

    with patch("app.api.routes.market_data.get_broker", return_value=broker), \
         patch("app.api.routes.market_data.settings.broker", "ibkr"), \
         patch("app.api.routes.market_data.settings.ibkr_trading_mode", "paper"), \
         patch("app.api.routes.market_data.settings.ibkr_port", 7497):
        result = await get_broker_status()

    assert result["status"] == "connected"
    assert result["paper_mode"] is True


@pytest.mark.asyncio
async def test_ibkr_disconnected_when_socket_reports_false_despite_flag():
    """A hardcoded `_connected=True` must not paper over a dropped socket —
    isConnected() is the real signal."""
    broker = MagicMock()
    broker.supports_options = True
    broker.supports_equities = True
    broker._connected = True
    broker.ib = MagicMock()
    broker.ib.isConnected.return_value = False

    with patch("app.api.routes.market_data.get_broker", return_value=broker), \
         patch("app.api.routes.market_data.settings.broker", "ibkr"):
        result = await get_broker_status()

    assert result["status"] == "disconnected"


@pytest.mark.asyncio
async def test_alpaca_connected_via_flag_alone_no_socket():
    """Alpaca has no `.ib` socket object — connectivity is just its own
    _connected flag, not a broken isConnected() lookup."""
    broker = MagicMock(spec=["supports_options", "supports_equities", "_connected"])
    broker.supports_options = True
    broker.supports_equities = True
    broker._connected = True

    with patch("app.api.routes.market_data.get_broker", return_value=broker), \
         patch("app.api.routes.market_data.settings.broker", "alpaca"), \
         patch("app.api.routes.market_data.settings.alpaca_base_url", "https://paper-api.alpaca.markets"):
        result = await get_broker_status()

    assert result["status"] == "connected"
    assert result["paper_mode"] is True


@pytest.mark.asyncio
async def test_alpaca_live_base_url_is_not_paper_mode():
    broker = MagicMock(spec=["supports_options", "supports_equities", "_connected"])
    broker.supports_options = True
    broker.supports_equities = True
    broker._connected = True

    with patch("app.api.routes.market_data.get_broker", return_value=broker), \
         patch("app.api.routes.market_data.settings.broker", "alpaca"), \
         patch("app.api.routes.market_data.settings.alpaca_base_url", "https://api.alpaca.markets"):
        result = await get_broker_status()

    assert result["paper_mode"] is False


@pytest.mark.asyncio
async def test_broker_error_falls_back_to_error_status():
    with patch("app.api.routes.market_data.get_broker", side_effect=RuntimeError("boom")), \
         patch("app.api.routes.market_data.settings.broker", "alpaca"):
        result = await get_broker_status()

    assert result["status"] == "error"
    assert "boom" in result["error"]


# ── Why, not just whether ───────────────────────────────────────────────────
#
# Production, 2026-10-31: the ENV chip read DISCONNECTED and every signal card
# was blocked. The chip's tooltip renders `error` from this route, but only the
# exception branch ever set it — a dropped socket returned a bare
# "disconnected", so the reason was reachable only by reading container logs on
# the host. The operator could see that the desk was down and nothing about why.


@pytest.mark.asyncio
async def test_a_disconnected_broker_says_why():
    broker = MagicMock()
    broker.supports_options = True
    broker.supports_equities = True
    broker._connected = False
    broker._last_connect_error = (
        "ConnectionRefusedError: [Errno 111] Connection refused "
        "(host=ibkr-gateway port=4004 clientId=7, attempt 3/3)"
    )
    broker.ib = MagicMock()
    broker.ib.isConnected.return_value = False

    with patch("app.api.routes.market_data.get_broker", return_value=broker), \
         patch("app.api.routes.market_data.settings.broker", "ibkr"), \
         patch("app.api.routes.market_data.settings.ibkr_trading_mode", "paper"), \
         patch("app.api.routes.market_data.settings.ibkr_port", 7497):
        result = await get_broker_status()

    assert result["status"] == "disconnected"
    assert "Connection refused" in result["error"]
    assert "port=4004" in result["error"]


@pytest.mark.asyncio
async def test_it_still_answers_when_no_attempt_has_been_recorded():
    """A broker that has never tried to connect has no stored error. The chip
    must still say something actionable rather than fall back to silence."""
    broker = MagicMock(spec=["supports_options", "supports_equities", "_connected", "ib"])
    broker.supports_options = True
    broker.supports_equities = True
    broker._connected = False
    broker.ib = MagicMock()
    broker.ib.isConnected.return_value = False

    with patch("app.api.routes.market_data.get_broker", return_value=broker), \
         patch("app.api.routes.market_data.settings.broker", "ibkr"), \
         patch("app.api.routes.market_data.settings.ibkr_trading_mode", "paper"), \
         patch("app.api.routes.market_data.settings.ibkr_port", 7497), \
         patch("app.api.routes.market_data.settings.ibkr_host", "ibkr-gateway"):
        result = await get_broker_status()

    assert result["status"] == "disconnected"
    assert "ibkr-gateway:7497" in result["error"]


@pytest.mark.asyncio
async def test_a_connected_broker_carries_no_error_field():
    """`error` present must mean something is wrong — not be ambient noise."""
    broker = MagicMock()
    broker.supports_options = True
    broker.supports_equities = True
    broker._connected = True
    broker.ib = MagicMock()
    broker.ib.isConnected.return_value = True

    with patch("app.api.routes.market_data.get_broker", return_value=broker), \
         patch("app.api.routes.market_data.settings.broker", "ibkr"), \
         patch("app.api.routes.market_data.settings.ibkr_trading_mode", "paper"), \
         patch("app.api.routes.market_data.settings.ibkr_port", 7497):
        result = await get_broker_status()

    assert result["status"] == "connected"
    assert "error" not in result


@pytest.mark.asyncio
async def test_the_reason_never_carries_an_account_number_or_token():
    """
    This string is rendered in a browser tooltip, so it must stay config-only:
    host, port, clientId. A broker that stored something sensitive in its last
    error would publish it to anyone with the page open.
    """
    broker = MagicMock()
    broker.supports_options = True
    broker.supports_equities = True
    broker._connected = False
    broker._last_connect_error = "ConnectionRefusedError: refused (host=h port=1 clientId=7)"
    broker.ib = MagicMock()
    broker.ib.isConnected.return_value = False

    with patch("app.api.routes.market_data.get_broker", return_value=broker), \
         patch("app.api.routes.market_data.settings.broker", "ibkr"), \
         patch("app.api.routes.market_data.settings.ibkr_trading_mode", "paper"), \
         patch("app.api.routes.market_data.settings.ibkr_port", 7497):
        result = await get_broker_status()

    blob = result["error"].lower()
    for forbidden in ("account", "token", "secret", "password", "u1234", "du1234"):
        assert forbidden not in blob, f"{forbidden!r} leaked into a browser-visible string"
