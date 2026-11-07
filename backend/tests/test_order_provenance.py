"""Which program placed an order, read off IBKR's own record of it.

Written because two open positions (FER, AMZN) had brackets resting at IBKR but
no record anywhere in the app of having sent them. The app logs its own orders;
it cannot log someone else's. IBKR, though, stamps every order with the client ID
of the API client that sent it, so the sender can be read back from the broker
rather than inferred from silence in our own logs.

What matters:

  * the label is judged from the broker's field, never assumed "ours";
  * a different program, TWS/manual entry, and our own app are three different
    answers and must stay distinguishable;
  * an order the broker doesn't identify is "unknown", not quietly "ours";
  * adding the fields changes nothing the existing readers (stop backfill, orphan
    sweep, rotation preflight, the open-orders page) rely on;
  * it is a read: nothing is placed, cancelled or modified.

The test orders are built from the real ib_insync types, so a misspelt attribute
name cannot make a test pass for the wrong reason.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from ib_insync import Order, OrderStatus, Stock

from app.api.routes.portfolio import portfolio_open_orders
from app.broker.ibkr_client import IBKRClient, order_source
from app.core.config import settings
from conftest import single_operator_conn

OWN = 2   # the id this server connects with


# ── the label ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("client_id, expected", [
    (2, "this_app"),
    (0, "tws_or_manual"),
    (1, "other_api_client"),         # the code's own default id is 1: not ours here
    (7, "other_api_client"),
    (999, "other_api_client"),
    (None, "unknown"),
    (-1, "unknown"),
    (True, "unknown"),               # a bool is not a client id
    ("abc", "unknown"),
    ("", "unknown"),
])
def test_the_label_is_read_from_the_brokers_client_id(client_id, expected):
    assert order_source(client_id, OWN) == expected


def test_an_id_that_arrives_as_text_is_still_read():
    assert order_source("2", OWN) == "this_app"
    assert order_source("0", OWN) == "tws_or_manual"
    assert order_source("7", OWN) == "other_api_client"


def test_our_own_id_wins_even_if_it_is_zero():
    """If this app were the master client (id 0) its own orders are 'ours', not
    'manual'. Own is checked first."""
    assert order_source(0, 0) == "this_app"
    assert order_source(3, 0) == "other_api_client"


def test_a_bad_own_id_makes_everything_unknown_rather_than_ours():
    for own in (None, "x"):
        assert order_source(2, own) == "unknown"
        assert order_source(0, own) == "unknown"


# ── reading the book ───────────────────────────────────────────────────────

def _trade(symbol, order_id, *, client_id, perm_id=0, ref="", account="", parent=0,
           action="SELL", otype="STP", qty=10, aux=0.0, lmt=0.0):
    order = Order(
        orderId=order_id, clientId=client_id, permId=perm_id, orderRef=ref,
        account=account, parentId=parent, action=action, orderType=otype,
        totalQuantity=qty, auxPrice=aux, lmtPrice=lmt, tif="GTC",
    )
    return SimpleNamespace(
        order=order, contract=Stock(symbol, "SMART", "USD"),
        orderStatus=OrderStatus(orderId=order_id, status="PreSubmitted",
                                filled=0.0, remaining=float(qty)),
    )


class _FakeIB:
    def __init__(self, trades, refresh_fails=False):
        self._trades = trades
        self._refresh_fails = refresh_fails
        self.refreshed = 0
        self.touched_orders = []

    def isConnected(self):
        return True

    async def reqAllOpenOrdersAsync(self):
        self.refreshed += 1
        if self._refresh_fails:
            raise ConnectionError("gateway dropped")

    def openTrades(self):
        return self._trades

    # A read must never be able to place, cancel or modify. Any of these being
    # called would be recorded here and fail the test that checks it.
    def placeOrder(self, *a, **k):
        self.touched_orders.append("place")

    def cancelOrder(self, *a, **k):
        self.touched_orders.append("cancel")


def _client(trades, **kw):
    c = IBKRClient()
    c._connected = True
    c.ib = _FakeIB(trades, **kw)
    return c


@pytest.fixture(autouse=True)
def _own_id(monkeypatch):
    monkeypatch.setattr(settings, "ibkr_client_id", OWN)


def _by_id(result):
    return {o["order_id"]: o for o in result["orders"]}


def test_each_order_reports_who_sent_it():
    c = _client([
        _trade("AMZN", 980078, client_id=2, perm_id=111, ref="", account="DU123", parent=980076),
        _trade("FER", 954549, client_id=0, perm_id=222),
        _trade("SPCX", 980277, client_id=7, perm_id=333, ref="bot-A", account="DU123"),
        _trade("XYZ", 5, client_id=2, perm_id=0),
    ])
    out = asyncio.run(c.get_open_orders())
    orders = _by_id(out)

    assert orders[980078]["client_id"] == 2 and orders[980078]["placed_by"] == "this_app"
    assert orders[954549]["client_id"] == 0 and orders[954549]["placed_by"] == "tws_or_manual"
    assert orders[980277]["client_id"] == 7 and orders[980277]["placed_by"] == "other_api_client"
    assert orders[980277]["order_ref"] == "bot-A"
    assert orders[980078]["perm_id"] == 111 and orders[980078]["account"] == "DU123"
    # IBKR's 0 / empty-string "not set" values are reported as None, not as data
    assert orders[5]["perm_id"] is None and orders[5]["order_ref"] is None
    assert orders[954549]["account"] is None
    assert out["app_client_id"] == OWN


def test_an_order_without_a_client_id_is_unknown_not_ours():
    trade = _trade("AMZN", 1, client_id=2)
    trade.order.clientId = None                      # the broker did not say
    out = asyncio.run(_client([trade]).get_open_orders())
    assert out["orders"][0]["placed_by"] == "unknown"


def test_the_fields_are_added_without_changing_any_existing_one():
    """Stop backfill, the orphan sweep, rotation preflight and the open-orders
    page all read these keys. They must be exactly as they were."""
    out = asyncio.run(_client([
        _trade("AMZN", 980078, client_id=2, aux=243.82),
        _trade("AMZN", 980077, client_id=2, otype="LMT", lmt=298.96),
    ]).get_open_orders())
    legacy = {
        "order_id", "parent_id", "symbol", "sec_type", "action", "order_type",
        "quantity", "limit_price", "stop_price", "tif", "status", "filled",
        "remaining", "is_protective",
    }
    for o in out["orders"]:
        assert legacy <= set(o)
    stop = _by_id(out)[980078]
    assert (stop["stop_price"], stop["is_protective"], stop["symbol"]) == (243.82, True, "AMZN")
    limit = _by_id(out)[980077]
    assert (limit["limit_price"], limit["is_protective"]) == (298.96, False)
    assert out["source"] == "refreshed"


def test_a_failed_refresh_still_reports_the_sender_and_says_the_read_was_stale():
    out = asyncio.run(_client([_trade("AMZN", 1, client_id=7)], refresh_fails=True).get_open_orders())
    assert out["source"] == "cache_after_refresh_failed"
    assert out["orders"][0]["placed_by"] == "other_api_client"
    assert out["app_client_id"] == OWN


def test_reading_the_book_places_cancels_and_modifies_nothing():
    c = _client([_trade("AMZN", 1, client_id=7), _trade("FER", 2, client_id=0)])
    asyncio.run(c.get_open_orders())
    assert c.ib.touched_orders == []
    assert c.ib.refreshed == 1                       # it did ask the broker


def test_the_label_follows_the_configured_client_id(monkeypatch):
    """Not a hard-coded 2: change the server's id and the same order flips."""
    c = _client([_trade("AMZN", 1, client_id=2)])
    assert asyncio.run(c.get_open_orders())["orders"][0]["placed_by"] == "this_app"
    monkeypatch.setattr(settings, "ibkr_client_id", 5)
    out = asyncio.run(c.get_open_orders())
    assert out["orders"][0]["placed_by"] == "other_api_client" and out["app_client_id"] == 5


# ── the page ───────────────────────────────────────────────────────────────

def _session(trades):
    s = AsyncMock()
    s.__aenter__ = AsyncMock(return_value=s)
    s.__aexit__ = AsyncMock(return_value=False)
    res = MagicMock()
    res.scalars.return_value = MagicMock(all=lambda: trades)
    s.execute = AsyncMock(return_value=res)
    return s


def test_the_open_orders_page_carries_the_senders_through():
    book = {
        "source": "refreshed", "app_client_id": OWN,
        "orders": [{
            "order_id": 954549, "parent_id": None, "symbol": "FER", "sec_type": "STK",
            "action": "BUY", "order_type": "STP", "quantity": 337.0, "limit_price": None,
            "stop_price": 61.57, "tif": "GTC", "status": "PreSubmitted", "filled": 0.0,
            "remaining": 337.0, "is_protective": True,
            "client_id": 7, "perm_id": 222, "order_ref": None, "account": "DU123",
            "placed_by": "other_api_client",
        }],
    }
    trade = SimpleNamespace(underlying="FER", quantity=337, spread_type="equity_short",
                            strategy="adopted_untracked")
    broker = MagicMock()
    broker.get_open_orders = AsyncMock(return_value=book)
    with patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.core.database.AsyncSessionLocal", return_value=_session([trade])), \
         patch("app.broker.ibkr_coordinator.ibkr_coordinator.submit", new=AsyncMock(return_value=book)):
        out = asyncio.run(portfolio_open_orders(single_operator_conn()))
    assert out["app_client_id"] == OWN
    sent = out["orders"][0]
    assert sent["placed_by"] == "other_api_client" and sent["client_id"] == 7
    assert out["positions_without_stop"] == [] and out["protected_tickers"] == ["FER"]
