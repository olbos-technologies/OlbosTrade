"""Only an authoritative absence may release a position claim.

Three ways to wrongly conclude "no such order", each of which frees a claim
guarding a possibly-live position:

* the broker cannot look orders up by client id at all,
* the order exists but is not visible yet,
* the key was never sent to the broker, so nothing could ever carry it.

The first and third are this module's job; the second belongs to
reconcile_unresolved's settle window. All three must come out INDETERMINATE.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.broker.broker_interface import OrderLookup
from app.services.claim_lookup import KEYED_ASSET_CLASSES, lookup_for
from app.services.position_claim import BrokerVerdict


def _claim(asset_class="options", key="oc-abc123"):
    return SimpleNamespace(
        claim_token="tok-1",
        underlying="SPY",
        asset_class=asset_class,
        idempotency_key=key,
    )


def _broker(result):
    broker = MagicMock()
    broker.find_order_by_client_order_id = AsyncMock(return_value=result)
    return broker


# ── mapping ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_found_order_is_present():
    verdict = await lookup_for(_broker(OrderLookup.FOUND))(_claim())
    assert verdict is BrokerVerdict.PRESENT


@pytest.mark.asyncio
async def test_an_authoritative_not_found_is_absent():
    verdict = await lookup_for(_broker(OrderLookup.NOT_FOUND))(_claim())
    assert verdict is BrokerVerdict.ABSENT


@pytest.mark.asyncio
async def test_an_undetermined_lookup_is_indeterminate():
    verdict = await lookup_for(_broker(OrderLookup.UNDETERMINED))(_claim())
    assert verdict is BrokerVerdict.INDETERMINATE


@pytest.mark.asyncio
async def test_an_unrecognised_answer_is_indeterminate():
    """A broker returning something outside the enum must not release."""
    verdict = await lookup_for(_broker("no idea"))(_claim())
    assert verdict is BrokerVerdict.INDETERMINATE


# ── the three ways to be wrong ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_broker_that_cannot_look_orders_up_never_establishes_absence():
    """The interface default, which is what IBKR uses.

    A broker is opted IN to authoritative absence by overriding the method.
    Forgetting to override it cannot produce a release.
    """
    from app.broker.broker_interface import BrokerInterface

    broker = MagicMock()
    broker.find_order_by_client_order_id = BrokerInterface.find_order_by_client_order_id.__get__(broker)

    verdict = await lookup_for(broker)(_claim())
    assert verdict is BrokerVerdict.INDETERMINATE


@pytest.mark.asyncio
async def test_ibkr_does_not_override_the_lookup():
    """Stated directly, because the test above only proves the default works."""
    from app.broker.ibkr_client import IBKRClient

    assert "find_order_by_client_order_id" not in IBKRClient.__dict__, (
        "IBKR now claims it can establish absence — verify that its lookup is "
        "really indexed by client id before trusting a NOT_FOUND from it"
    )


@pytest.mark.asyncio
async def test_an_equity_claim_is_never_resolved_by_lookup():
    """The subtle one: the key was never transmitted.

    `place_equity_order` sends no client_order_id, so no equity order can
    carry this key. A broker's 404 is then true and meaningless — it says
    nothing about whether an equity position was opened. Releasing on it
    would free the claim on exactly the asset class with no broker-side dedup
    behind it.
    """
    broker = _broker(OrderLookup.NOT_FOUND)

    verdict = await lookup_for(broker)(_claim(asset_class="equity"))

    assert verdict is BrokerVerdict.INDETERMINATE
    broker.find_order_by_client_order_id.assert_not_awaited(), (
        "the broker should not even be asked about a key it never received"
    )


@pytest.mark.asyncio
async def test_only_options_are_keyed():
    assert KEYED_ASSET_CLASSES == frozenset({"options"})


@pytest.mark.asyncio
async def test_no_broker_resolves_nothing():
    """A sweep with no broker configured must be a no-op, not a mass release."""
    verdict = await lookup_for(None)(_claim())
    assert verdict is BrokerVerdict.INDETERMINATE


# ── the Alpaca adapter's own HTTP mapping ──────────────────────────────────

def _alpaca():
    """An AlpacaClient without going through credential setup."""
    from app.broker.alpaca_client import AlpacaClient

    client = object.__new__(AlpacaClient)
    client._trading_base = "https://paper-api.example"
    client._headers = {"Apca-Api-Key-Id": "k", "Apca-Api-Secret-Key": "s"}
    return client


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


def _http(resp=None, raises=None):
    client = MagicMock()
    client.get = AsyncMock(return_value=resp, side_effect=raises)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [
    (200, OrderLookup.FOUND),
    (404, OrderLookup.NOT_FOUND),
    (500, OrderLookup.UNDETERMINED),
    (502, OrderLookup.UNDETERMINED),
    (429, OrderLookup.UNDETERMINED),
    (403, OrderLookup.UNDETERMINED),
])
async def test_alpaca_maps_http_status_to_a_lookup(status, expected):
    """Only a 404 from the by-client-order-id endpoint is absence.

    A 500 or a 429 is the broker failing to answer. Reading either as absence
    would release claims precisely when the broker is unhealthy — the worst
    possible moment to assume nothing was submitted.
    """
    with patch("app.broker.alpaca_client.httpx.AsyncClient", _http(resp=_Resp(status))):
        result = await _alpaca().find_order_by_client_order_id("oc-abc")
    assert result is expected


@pytest.mark.asyncio
async def test_alpaca_treats_a_transport_failure_as_undetermined():
    with patch("app.broker.alpaca_client.httpx.AsyncClient",
               _http(raises=httpx.ConnectError("no route"))):
        result = await _alpaca().find_order_by_client_order_id("oc-abc")
    assert result is OrderLookup.UNDETERMINED


@pytest.mark.asyncio
async def test_alpaca_treats_a_timeout_as_undetermined():
    """A timeout is the canonical "I do not know"."""
    with patch("app.broker.alpaca_client.httpx.AsyncClient",
               _http(raises=httpx.ReadTimeout("slow"))):
        result = await _alpaca().find_order_by_client_order_id("oc-abc")
    assert result is OrderLookup.UNDETERMINED


@pytest.mark.asyncio
async def test_alpaca_queries_the_client_order_id_index():
    """Not the general order list, which would make an empty page look like absence."""
    http = _http(resp=_Resp(200))
    with patch("app.broker.alpaca_client.httpx.AsyncClient", http):
        await _alpaca().find_order_by_client_order_id("oc-abc")

    client = await http.return_value.__aenter__()
    url = client.get.await_args.args[0]
    params = client.get.await_args.kwargs["params"]
    assert url.endswith("/v2/orders:by_client_order_id")
    assert params == {"client_order_id": "oc-abc"}


# ── the interface contract itself ──────────────────────────────────────────

def test_the_broker_interfaces_abstract_set_is_pinned():
    """Which methods a broker MUST implement, stated explicitly.

    Adding `find_order_by_client_order_id` above `cancel_all_open_orders`
    silently moved that method's `@abstractmethod` onto the new one. Two bugs
    from one edit: the new method became abstract (so IBKRClient could not be
    instantiated at all, which 34 tests noticed), and
    `cancel_all_open_orders` quietly stopped being required — a broker could
    then ship with no kill-switch cancel sweep, and nothing would have said
    so. Only the first failure was loud.

    So the set is pinned. A method leaving this list is a weakened contract
    and has to be deliberate.
    """
    from app.broker.broker_interface import BrokerInterface

    assert set(BrokerInterface.__abstractmethods__) == {
        "cancel_all_open_orders",
        "cancel_open_orders",
        "get_account_summary",
        "get_bars",
        "get_greeks",
        "get_latest_quote",
        "get_options_chain",
        "get_positions",
        "place_equity_order",
        "place_order",
        "supports_equities",
        "supports_options",
    }


def test_the_lookup_is_concrete_so_a_broker_opts_in():
    """A broker must not be forced to claim it can establish absence.

    If this were abstract, every broker would have to implement it, and the
    cheapest implementation to satisfy a type checker returns something. The
    safe answer has to be the one you get by doing nothing.
    """
    from app.broker.broker_interface import BrokerInterface

    assert "find_order_by_client_order_id" not in BrokerInterface.__abstractmethods__
