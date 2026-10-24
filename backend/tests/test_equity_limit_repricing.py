"""Equity limit repricing behavior, independent of broker entry authorization.

The production execution route is currently fail-closed before broker entry.
These checks assert arithmetic and route integration statically; they do NOT
prove a priced order can reach a broker. That requires a separate authorized
submission integration test when R2-Q is implemented.
"""
from pathlib import Path
from types import SimpleNamespace

from app.services.equity_order_pricing import reprice_equity_limit


def _quote(bid, ask):
    return SimpleNamespace(bid_price=bid, ask_price=ask)


def test_buy_reprices_to_live_ask_with_marketable_buffer():
    price = reprice_equity_limit(
        action="BUY", fallback_price=150.0,
        quote=_quote(bid=159.90, ask=160.00), buffer=0.001,
    )
    assert 160.00 <= price < 160.20


def test_sell_reprices_to_live_bid_with_marketable_buffer():
    price = reprice_equity_limit(
        action="SELL", fallback_price=150.0,
        quote=_quote(bid=140.00, ask=140.10), buffer=0.001,
    )
    assert 139.80 < price <= 140.00


def test_falls_back_to_stale_entry_price_when_quote_unavailable():
    # Quote acquisition errors are caught by the execution route; the pure
    # helper accepts None when a quote could not be fetched.
    assert reprice_equity_limit(
        action="BUY", fallback_price=150.0, quote=None, buffer=0.001,
    ) == 150.0


def test_zero_ask_does_not_reprice_buy():
    assert reprice_equity_limit(
        action="BUY", fallback_price=150.0,
        quote=_quote(bid=0, ask=0), buffer=0.001,
    ) == 150.0


def test_execution_route_still_calls_repricing_helper():
    # Source-level integration check, NOT a broker-submission behavior proof.
    # Guard against accidentally orphaning the helper during route changes.
    route = Path(__file__).resolve().parents[1] / "app/api/routes/trade_desk.py"
    text = route.read_text()
    assert "from app.services.equity_order_pricing import reprice_equity_limit" in text
    assert "entry_price = reprice_equity_limit(" in text
