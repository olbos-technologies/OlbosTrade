"""Preparation-only tests: repricing must not authorize broker submission."""
from types import SimpleNamespace

from app.services.equity_order_pricing import reprice_equity_limit


def test_buy_uses_live_ask_with_buffer():
    quote = SimpleNamespace(ask_price=100.0, bid_price=99.0)
    assert reprice_equity_limit(action="BUY", fallback_price=80.0, quote=quote, buffer=0.01) == 101.0


def test_sell_uses_live_bid_with_buffer():
    quote = SimpleNamespace(ask_price=100.0, bid_price=99.0)
    assert reprice_equity_limit(action="SELL", fallback_price=80.0, quote=quote, buffer=0.01) == 98.01


def test_missing_quote_preserves_scan_price():
    assert reprice_equity_limit(action="BUY", fallback_price=80.0, quote=None, buffer=0.01) == 80.0


def test_zero_ask_preserves_scan_price():
    quote = SimpleNamespace(ask_price=0.0, bid_price=99.0)
    assert reprice_equity_limit(action="BUY", fallback_price=80.0, quote=quote, buffer=0.01) == 80.0


def test_unexpected_quote_shape_fails_conservatively():
    assert reprice_equity_limit(action="SELL", fallback_price=80.0, quote=object(), buffer=0.01) == 80.0


def test_nonfinite_quote_prices_are_not_propagated():
    from types import SimpleNamespace
    for invalid in (float("nan"), float("inf"), float("-inf")):
        quote = SimpleNamespace(ask_price=invalid, bid_price=invalid)
        assert reprice_equity_limit(
            action="BUY", fallback_price=80.0, quote=quote, buffer=0.01,
        ) == 80.0
        assert reprice_equity_limit(
            action="SELL", fallback_price=80.0, quote=quote, buffer=0.01,
        ) == 80.0


def test_invalid_or_overflowing_buffer_cannot_create_limit_price():
    from types import SimpleNamespace
    quote = SimpleNamespace(ask_price=100.0, bid_price=99.0)
    for invalid in (float("nan"), float("inf"), -0.1, 1.0):
        assert reprice_equity_limit(
            action="BUY", fallback_price=80.0, quote=quote, buffer=invalid,
        ) == 80.0
