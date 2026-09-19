"""
The orphan sweep cancels orders with no position, and nothing else.

Two failure directions, and they are not symmetrical:

  MISSING an orphan leaves a live stop that, if touched, OPENS an unintended
  position. Bad, and exactly what happened twice in production.

  CANCELLING a live position's bracket strips a real position of its
  protection. Worse — it converts a bounded trade into an unbounded one, and
  nothing downstream notices until the position moves.

So the sweep is asserted in both directions, and the guards that keep it on the
right side of the second are tested individually rather than trusted because
they are written down.
"""

from __future__ import annotations

import pytest

from app.services import orphan_order_sweep as sweep_mod
from app.services.orphan_order_sweep import MAX_CANCELS_PER_PASS, sweep_orphaned_orders


class _Pos:
    """Mirrors IBKRClient: equities set symbol == underlying, OPTIONS DO NOT.

    An option position carries symbol=localSymbol ("AAPL  260116C00150000")
    and underlying=c.symbol ("AAPL"), while its ORDER reports c.symbol.
    """
    def __init__(self, symbol: str, underlying: str | None = None):
        self.symbol = symbol
        self.underlying = underlying if underlying is not None else symbol


class FakeBroker:
    """Minimal stand-in exposing only what the sweep touches."""

    def __init__(self, orders, positions, source="refreshed"):
        self._orders = list(orders)
        self._positions = [_Pos(s) for s in positions]
        self._source = source
        self.cancelled: list[int] = []
        self.reads = 0
        self.position_reads = 0
        #: When set, the SECOND and later get_positions() calls return this
        #: instead — the sweep re-reads immediately before cancelling.
        self.positions_after: list[str] | None = None
        #: From the THIRD get_positions() onward — i.e. after the cancel, which
        #: is the only point at which a filled orphan can show up as a position.
        self.positions_final: list[str] | None = None
        #: Per-order-id override for what cancel_orders_by_id reports back.
        self.cancel_results: dict[int, str] = {}

    async def get_open_orders(self, refresh: bool = False):
        self.reads += 1
        return {"source": self._source, "orders": list(self._orders),
                "order_count": len(self._orders)}

    async def get_positions(self):
        """Serves `positions_after` from the second call onward, so a test can
        simulate a fill landing between the sweep's two position reads."""
        self.position_reads += 1
        # Read 1 = initial `held`. Read 2 = the pre-cancel re-read.
        # Read 3 = the post-cancel possible-fills check.
        if self.positions_final is not None and self.position_reads > 2:
            return [_Pos(s) for s in self.positions_final]
        if self.positions_after is not None and self.position_reads > 1:
            return [_Pos(s) for s in self.positions_after]
        return list(self._positions)

    async def cancel_orders_by_id(self, order_ids):
        self.cancelled.extend(order_ids)
        self._orders = [o for o in self._orders if o["order_id"] not in set(order_ids)]
        return [{"order_id": oid,
                 "result": self.cancel_results.get(oid, "cancel_sent")}
                for oid in order_ids]


def order(oid: int, symbol: str) -> dict:
    return {"order_id": oid, "symbol": symbol, "action": "BUY", "order_type": "STP"}


@pytest.fixture
def no_live_rows(monkeypatch):
    """Default: the DB agrees with the broker — no open/pending rows."""
    async def _none(symbols):
        return set()
    monkeypatch.setattr(sweep_mod, "_symbols_with_live_trade_rows", _none)


# ── The thing it exists to do ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_it_cancels_orders_whose_symbol_has_no_position(no_live_rows):
    """The 2026-09-19 shape: EXC/INTU/MU resting with nothing to protect."""
    broker = FakeBroker(
        orders=[order(1, "ALNY"), order(2, "ALNY"),
                order(3, "EXC"), order(4, "EXC"),
                order(5, "MU")],
        positions=["ALNY"],
    )
    report = await sweep_orphaned_orders(broker)
    assert report["status"] == "ok"
    assert sorted(broker.cancelled) == [3, 4, 5]
    assert report["by_symbol"] == {"EXC": 2, "MU": 1}
    assert report["confirmed_cancelled"] == [3, 4, 5]
    assert report["verified"] is True


@pytest.mark.asyncio
async def test_a_clean_book_cancels_nothing(no_live_rows):
    broker = FakeBroker(orders=[order(1, "ALNY"), order(2, "ALNY")],
                        positions=["ALNY"])
    report = await sweep_orphaned_orders(broker)
    assert report.get("nothing_to_do") is True
    assert broker.cancelled == []


# ── The failure that would be worse than the bug ─────────────────────────────

@pytest.mark.asyncio
async def test_it_never_cancels_an_order_for_a_held_position(no_live_rows):
    """Stripping a live position's stop converts a bounded trade into an
    unbounded one. No size, side or order type may change this."""
    broker = FakeBroker(
        orders=[order(1, "AVGO"), order(2, "AVGO"), order(3, "GONE")],
        positions=["AVGO"],
    )
    await sweep_orphaned_orders(broker)
    assert 1 not in broker.cancelled and 2 not in broker.cancelled, (
        "the sweep cancelled an order on a symbol the broker still holds"
    )
    assert broker.cancelled == [3]


@pytest.mark.asyncio
async def test_symbol_matching_is_case_insensitive(no_live_rows):
    """A case mismatch must fail toward SKIPPING the cancel.

    Same reasoning as main.py's `func.upper` on the DB side: if 'avgo' from the
    order book fails to match 'AVGO' from positions, the order looks orphaned
    and its position loses its stop.
    """
    broker = FakeBroker(orders=[order(1, "avgo")], positions=["AVGO"])
    await sweep_orphaned_orders(broker)
    assert broker.cancelled == [], (
        "case mismatch made a protected order look orphaned"
    )


@pytest.mark.asyncio
async def test_an_open_trade_row_defers_the_cancel(monkeypatch):
    """Broker says flat, DB says open — that disagreement is reconciliation's
    to resolve. Cancelling here would strip a bracket the DB still expects."""
    async def _live(symbols):
        return {"EXC"}
    monkeypatch.setattr(sweep_mod, "_symbols_with_live_trade_rows", _live)

    broker = FakeBroker(orders=[order(1, "EXC"), order(2, "MU")], positions=[])
    report = await sweep_orphaned_orders(broker)
    assert broker.cancelled == [2]
    assert report["deferred_symbols"] == ["EXC"]


# ── Aborts: every one of these would otherwise cancel real brackets ──────────

@pytest.mark.asyncio
async def test_a_cached_order_book_aborts_the_whole_sweep(no_live_rows):
    """A partial list from cache is indistinguishable from 'no orders here'."""
    broker = FakeBroker(orders=[order(1, "EXC")], positions=[], source="cache")
    report = await sweep_orphaned_orders(broker)
    assert report["status"] == "skipped"
    assert "cache" in report["reason"]
    assert broker.cancelled == []


@pytest.mark.asyncio
async def test_unreadable_positions_abort_the_sweep(no_live_rows):
    """With no positions every order looks orphaned — the worst possible
    moment to guess."""
    broker = FakeBroker(orders=[order(1, "AVGO"), order(2, "ALNY")],
                        positions=["AVGO", "ALNY"])

    async def _boom():
        raise RuntimeError("IBKR timeout")
    broker.get_positions = _boom

    report = await sweep_orphaned_orders(broker)
    assert report["status"] == "skipped"
    assert "positions" in report["reason"]
    assert broker.cancelled == []


@pytest.mark.asyncio
async def test_unreadable_trade_rows_abort_the_sweep(monkeypatch):
    async def _boom(symbols):
        raise RuntimeError("db down")
    monkeypatch.setattr(sweep_mod, "_symbols_with_live_trade_rows", _boom)

    broker = FakeBroker(orders=[order(1, "EXC")], positions=[])
    report = await sweep_orphaned_orders(broker)
    assert report["status"] == "skipped"
    assert broker.cancelled == []


@pytest.mark.asyncio
async def test_an_implausible_number_of_orphans_cancels_nothing(no_live_rows):
    """A book where everything looks orphaned is more likely a broken read.

    The real incidents were 20 and 18 orders. Wanting to cancel far more than
    that is a reason to stop and report, not to proceed confidently.
    """
    n = MAX_CANCELS_PER_PASS + 1
    broker = FakeBroker(orders=[order(i, f"SYM{i}") for i in range(n)], positions=[])
    report = await sweep_orphaned_orders(broker)
    assert report["status"] == "skipped"
    assert broker.cancelled == []
    assert report["by_symbol"], "the report must still say what it saw"


@pytest.mark.asyncio
async def test_exactly_the_limit_is_still_swept(no_live_rows):
    """Guards the guard above: an off-by-one that refused at the boundary
    would quietly stop sweeping real books."""
    broker = FakeBroker(
        orders=[order(i, f"SYM{i}") for i in range(MAX_CANCELS_PER_PASS)],
        positions=[],
    )
    report = await sweep_orphaned_orders(broker)
    assert report["status"] == "ok"
    assert len(broker.cancelled) == MAX_CANCELS_PER_PASS


# ── Verification, because a sent cancel is not a cancelled order ─────────────

@pytest.mark.asyncio
async def test_an_order_still_resting_after_cancel_is_reported(no_live_rows):
    """The 2026-08-29 failure: cancelling in TWS reported nothing and changed
    nothing. Trusting the send would have hidden it."""
    broker = FakeBroker(orders=[order(1, "EXC")], positions=[])

    async def _cancel_that_does_nothing(order_ids):
        broker.cancelled.extend(order_ids)
        return [{"order_id": oid, "result": "cancel_sent"} for oid in order_ids]
    broker.cancel_orders_by_id = _cancel_that_does_nothing

    report = await sweep_orphaned_orders(broker)
    assert report["confirmed_cancelled"] == []
    assert report["still_open"] == [1]


@pytest.mark.asyncio
async def test_dry_run_reports_without_cancelling(no_live_rows):
    broker = FakeBroker(orders=[order(1, "EXC"), order(2, "MU")], positions=[])
    report = await sweep_orphaned_orders(broker, dry_run=True)
    assert report["dry_run"] is True
    assert sorted(report["would_cancel"]) == [1, 2]
    assert broker.cancelled == []


@pytest.mark.asyncio
async def test_a_broker_that_cannot_cancel_by_id_is_skipped():
    class Limited:
        async def get_open_orders(self, refresh=False):
            return {"source": "refreshed", "orders": []}

    report = await sweep_orphaned_orders(Limited())
    assert report["status"] == "skipped"
    assert "cancel by id" in report["reason"]


# ── An order the sweep cannot identify ───────────────────────────────────────

@pytest.mark.asyncio
async def test_an_order_with_no_symbol_is_never_cancelled(no_live_rows):
    """"" is never in `held`, so a naive comparison calls it an orphan.

    Cancelling on the strength of a MISSING field is failing toward the cancel,
    which is the one direction this module must never fail in. Caught by
    Sourcery on #69.
    """
    broker = FakeBroker(
        orders=[{"order_id": 1, "symbol": None},
                {"order_id": 2, "symbol": "   "},
                {"order_id": 3, "symbol": ""},
                order(4, "EXC")],
        positions=[],
    )
    report = await sweep_orphaned_orders(broker)
    assert broker.cancelled == [4], (
        "the sweep cancelled an order it could not identify a symbol for"
    )
    assert report["ignored_no_symbol"] == 3


@pytest.mark.asyncio
async def test_symbolless_orders_do_not_hide_a_clean_book(no_live_rows):
    """The ignore path must not become a silent pass — the count is reported
    so an order book returning entries without symbols is visible."""
    broker = FakeBroker(
        orders=[{"order_id": 1, "symbol": None}, order(2, "ALNY")],
        positions=["ALNY"],
    )
    report = await sweep_orphaned_orders(broker)
    assert report.get("nothing_to_do") is True
    assert report["ignored_no_symbol"] == 1
    assert broker.cancelled == []


# ── The race between reading positions and cancelling ────────────────────────

@pytest.mark.asyncio
async def test_a_position_appearing_before_the_cancel_spares_its_bracket(no_live_rows):
    """A fill between the position read and the cancel would otherwise strip a
    live position's stop — the worst outcome this module can produce."""
    broker = FakeBroker(orders=[order(1, "EXC"), order(2, "MU")], positions=[])
    broker.positions_after = ["EXC"]          # EXC fills mid-sweep

    report = await sweep_orphaned_orders(broker)
    assert 1 not in broker.cancelled, (
        "cancelled the bracket of a position that appeared before the cancel"
    )
    assert broker.cancelled == [2]
    assert report["raced_symbols"] == ["EXC"]


@pytest.mark.asyncio
async def test_everything_racing_cancels_nothing(no_live_rows):
    broker = FakeBroker(orders=[order(1, "EXC")], positions=[])
    broker.positions_after = ["EXC"]
    report = await sweep_orphaned_orders(broker)
    assert broker.cancelled == []
    assert report.get("nothing_to_do") is True
    assert report["raced_symbols"] == ["EXC"]


@pytest.mark.asyncio
async def test_a_failed_reread_aborts_rather_than_cancelling(no_live_rows):
    """An unverifiable position list is exactly when not to act."""
    broker = FakeBroker(orders=[order(1, "EXC")], positions=[])

    calls = {"n": 0}
    original = broker.get_positions

    async def _fail_second_time():
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("IBKR dropped")
        return await original()
    broker.get_positions = _fail_second_time

    report = await sweep_orphaned_orders(broker)
    assert report["status"] == "skipped"
    assert "re-read" in report["reason"]
    assert broker.cancelled == []


@pytest.mark.asyncio
async def test_dry_run_does_not_need_the_second_read(no_live_rows):
    """Dry run reports intent without touching anything, so it returns before
    the re-read — asserted so the re-read is never quietly made unreachable."""
    broker = FakeBroker(orders=[order(1, "EXC")], positions=[])
    report = await sweep_orphaned_orders(broker, dry_run=True)
    assert report["would_cancel"] == [1]
    assert broker.position_reads == 1


# ── Options report a different symbol than their orders ──────────────────────

@pytest.mark.asyncio
async def test_a_live_option_positions_bracket_is_not_an_orphan(no_live_rows):
    """The HIGH one. IBKRClient gives an option position symbol=localSymbol and
    underlying=c.symbol, while its order reports c.symbol. Matching only on
    `symbol` means a live option position never covers its own order."""
    broker = FakeBroker(orders=[order(1, "AAPL")], positions=[])
    broker._positions = [_Pos("AAPL  260116C00150000", underlying="AAPL")]

    await sweep_orphaned_orders(broker)
    assert broker.cancelled == [], (
        "cancelled the bracket of a LIVE option position — the order book "
        "reports the underlying, the position reports the localSymbol"
    )


@pytest.mark.asyncio
async def test_an_option_underlying_with_no_position_is_still_swept(no_live_rows):
    """Guards the guard: matching on underlying must not spare everything."""
    broker = FakeBroker(orders=[order(1, "AAPL"), order(2, "MSFT")], positions=[])
    broker._positions = [_Pos("AAPL  260116C00150000", underlying="AAPL")]

    await sweep_orphaned_orders(broker)
    assert broker.cancelled == [2]


# ── A cancel that was not a cancel ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_not_found_is_not_counted_as_a_confirmed_cancel(no_live_rows):
    """not_found means the order left the book — possibly by FILLING.

    Counting that as a confirmed cancel reports the exact failure this module
    exists to prevent as a success.
    """
    broker = FakeBroker(orders=[order(1, "EXC"), order(2, "MU")], positions=[])
    broker.cancel_results = {1: "not_found"}

    report = await sweep_orphaned_orders(broker)
    assert report["confirmed_cancelled"] == [2]
    assert report["not_found"] == [1]


@pytest.mark.asyncio
async def test_a_not_found_order_whose_symbol_now_holds_a_position_is_flagged(no_live_rows):
    """The bad case made loud: the orphan FILLED and opened a position.

    The position must appear only AFTER the cancel. Making it appear earlier
    exercises the pre-cancel race guard instead — which is correct behaviour
    and strictly safer, but a different path, and asserting on it here would
    have tested the wrong thing while looking like it passed.
    """
    broker = FakeBroker(orders=[order(1, "EXC"), order(2, "MU")], positions=[])
    broker.cancel_results = {1: "not_found"}
    broker.positions_final = ["EXC"]   # the fill shows up only afterwards

    report = await sweep_orphaned_orders(broker)
    assert report["not_found"] == [1]
    assert 1 not in report["confirmed_cancelled"]
    assert report["possible_fills"] == ["EXC"], (
        "an orphan that vanished and left a position behind is the unintended "
        "position opening — it must be reported, not counted as a clean cancel"
    )


@pytest.mark.asyncio
async def test_a_broker_error_is_not_a_confirmed_cancel(no_live_rows):
    broker = FakeBroker(orders=[order(1, "EXC")], positions=[])
    broker.cancel_results = {1: "error"}
    report = await sweep_orphaned_orders(broker)
    assert report["confirmed_cancelled"] == []
    assert report["errored"] == [1]


# ── The verification read is itself verified ─────────────────────────────────

@pytest.mark.asyncio
async def test_a_cached_verification_read_confirms_nothing(no_live_rows):
    """get_open_orders(refresh=True) falls back to cache_after_refresh_failed.
    An empty cache shows every order as gone — marking them all confirmed
    while they are still resting."""
    broker = FakeBroker(orders=[order(1, "EXC")], positions=[])

    original = broker.get_open_orders
    calls = {"n": 0}

    async def _cache_on_verification(refresh: bool = False):
        calls["n"] += 1
        result = await original(refresh=refresh)
        if calls["n"] > 1:
            return {"source": "cache_after_refresh_failed", "orders": []}
        return result
    broker.get_open_orders = _cache_on_verification

    report = await sweep_orphaned_orders(broker)
    assert report["verified"] is False
    assert report["confirmed_cancelled"] == [], (
        "an empty cache was treated as proof the orders are gone"
    )
