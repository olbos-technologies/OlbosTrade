"""
The per-status tally is populated by the code that actually flattens.

test_kill_switch_reports_what_it_did.py asserts the ROUTE forwards
`flatten_statuses` — but it mocks `kill_switch_service.engage` with a prebuilt
report, so it proves nothing about whether anything ever fills that dict. Delete
the two lines in `_flatten_position` / `_flatten_equity` that populate it and
that suite stays green, while Risk Monitor shows "0 filled" for every real
kill-switch event: the operator is told nothing closed during the one action
where that claim matters most.

Caught in review on PR #64, and it is the second time this session: a test in
PR #61 asserted signal geometry against an explicitly-constructed params object
while every live caller passes None and gets an import-time singleton. Mocking
the unit under test at the seam you care about is how a suite comes to agree
with itself.

So these call engage() for real, with only the broker mocked, and assert the
tally against what the broker returned.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.kill_switch import KillSwitch


def _opt(underlying="SPY", qty=-1):
    return SimpleNamespace(
        symbol=f"{underlying} 261218P00400000", underlying=underlying,
        strike=Decimal("400"), expiration=date(2026, 12, 18), option_type="put",
        quantity=qty, avg_cost=Decimal("1.0"), asset_type="option",
    )


def _equity(symbol="AAPL", qty=100):
    return SimpleNamespace(
        symbol=symbol, underlying=symbol, strike=Decimal("0"),
        expiration=date(2026, 12, 18), option_type="call",
        quantity=qty, avg_cost=Decimal("100.0"), asset_type="equity",
    )


def _service(positions, statuses):
    """A service whose broker returns `positions` and fills orders with
    `statuses` in sequence."""
    svc = KillSwitch()
    broker = SimpleNamespace()
    broker.ib = None
    broker.get_positions = AsyncMock(return_value=positions)
    seq = list(statuses)
    async def _place(*_a, **_k):
        return SimpleNamespace(status=seq.pop(0), order_id="o", message="")
    broker.place_order = AsyncMock(side_effect=_place)
    broker.place_equity_order = AsyncMock(side_effect=_place)
    svc.configure(broker)
    return svc


async def _engage(svc):
    # The DB write is the last step and is independent of the tally; stubbing
    # it keeps these tests off a live Postgres without touching what they test.
    with patch("app.services.kill_switch.AsyncSessionLocal", side_effect=RuntimeError("no db")):
        return await svc.engage("test")


@pytest.mark.asyncio
async def test_a_real_options_flatten_records_its_status():
    svc = _service([_opt()], ["filled"])

    out = await _engage(svc)

    assert out["positions_flattened"] == 1
    assert out["flatten_statuses"] == {"filled": 1}


@pytest.mark.asyncio
async def test_a_real_equity_flatten_records_its_status():
    svc = _service([_equity()], ["filled"])

    out = await _engage(svc)

    assert out["flatten_statuses"] == {"filled": 1}


@pytest.mark.asyncio
async def test_submitted_and_partial_are_counted_but_not_as_filled():
    """The whole point. positions_flattened counts these; only `filled` closed."""
    svc = _service([_opt(), _opt("QQQ"), _opt("IWM")],
                   ["filled", "submitted", "partial"])

    out = await _engage(svc)

    assert out["positions_flattened"] == 3
    assert out["flatten_statuses"] == {"filled": 1, "submitted": 1, "partial": 1}
    # Three orders went out; one position is actually gone.
    assert out["flatten_statuses"].get("filled", 0) == 1


@pytest.mark.asyncio
async def test_a_rejected_order_is_an_error_not_a_tally_entry():
    svc = _service([_opt()], ["rejected"])

    out = await _engage(svc)

    assert out["positions_flattened"] == 0
    assert out["flatten_statuses"] == {}
    assert any("flatten" in e for e in out["errors"])


@pytest.mark.asyncio
async def test_a_mixed_book_tallies_both_paths():
    """Options go through _flatten_position, equities through _flatten_equity —
    both must populate the same dict or the report is half-blind."""
    svc = _service([_opt(), _equity()], ["filled", "submitted"])

    out = await _engage(svc)

    assert sum(out["flatten_statuses"].values()) == 2
    assert set(out["flatten_statuses"]) == {"filled", "submitted"}
