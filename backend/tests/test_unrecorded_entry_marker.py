"""An entry that ends without a trade row must say so, loudly and searchably.

Observed 2026-11: the desk's own equity entries (AMZN, FER) appeared on screen as
"Adopted from broker", and 14 equity claims sat `submitted` for good. Both are what a
submitted-but-never-recorded entry looks like, and the code gave no way to tell which
path produced it: the outer handler of `_execute_signal` recorded a counter and
wrote NO log line, so an exception after the broker call left nothing to read.

These pin the marker (ENTRY_UNRECORDED + counter + event) on every path that ends
that way, and its absence on every path that does not — a marker that fires on
healthy entries would be as useless as none.

No order behaviour changes: the same result dicts come back as before.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes.trade_desk import _execute_signal
from app.services.observability import observability
from app.services.guardrails import PortfolioState

MARK = "ENTRY_UNRECORDED"


@pytest.fixture(autouse=True)
def _market_open():
    with patch("app.utils.market_hours.is_market_open", return_value=True):
        yield


def _portfolio():
    return PortfolioState(current_value=100_000.0, starting_capital=100_000.0,
                          daily_pnl=0.0, weekly_pnl=0.0, monthly_pnl=0.0,
                          consecutive_losses=0, trades_today=0)


def _equity_signal():
    return {
        "id": "sig-marker", "ticker": "AMZN", "action": "BUY", "asset_type": "equity",
        "confidence": 0.95,
        "trade_plan": {"shares": 7, "entry_price": 262.0, "stop_price": None,
                       "target_price": None, "risk_reward": 2.0},
        "indicators": {"volume_ratio": 1.5},
        "signal_score": 0.8, "iv_rank": 30.0, "regime": "normal",
    }


def _options_signal():
    return {
        "id": "sig-marker-opt", "ticker": "SPY", "asset_type": "options",
        "strategy": "bull_put_spread", "action": "SELL", "quantity": 1,
        "spread": {"short_strike": 450.0, "long_strike": 445.0,
                   "expiration": "2030-01-18", "option_type": "put", "net_credit": 100.0},
        "confidence": 0.9, "signal_score": 0.8, "iv_rank": 30.0, "regime": "normal",
    }


def _broker(*, equity=None, options=None):
    b = MagicMock()
    b.get_positions = AsyncMock(return_value=[])
    b.get_latest_quote = AsyncMock(return_value=MagicMock(bid_price=261.9, ask_price=262.1))
    b.get_account_summary = AsyncMock(return_value=MagicMock(
        net_liquidation=100_000.0, buying_power=100_000.0))
    b.place_equity_order = equity or AsyncMock(
        return_value=MagicMock(order_id="E-1", status="submitted"))
    b.place_order = options or AsyncMock(
        return_value=MagicMock(order_id="O-1", status="submitted"))
    return b


async def _run(signal, broker, *, record_fill, approved_by="autopilot"):
    async def _inline(_prio, fn, **_kw):
        return await fn()

    recorder = MagicMock()
    recorder.record_fill = record_fill
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=MagicMock(first=lambda: None))
    with patch("app.api.routes.trade_desk._fetch_portfolio_state", return_value=_portfolio()), \
         patch("app.api.routes.trade_desk._is_kill_switch_active", return_value=False), \
         patch("app.broker.broker_factory.get_broker", return_value=broker), \
         patch("app.api.routes.trade_desk.ibkr_coordinator.submit", new=_inline), \
         patch("app.services.trade_recorder.trade_recorder", recorder), \
         patch("app.core.database.AsyncSessionLocal", return_value=session):
        return await _execute_signal(signal, approved_by=approved_by)


def _marked(caplog):
    return [r for r in caplog.records if MARK in r.getMessage()]


@pytest.fixture(autouse=True)
def _fresh_counters():
    observability.reset()
    yield
    observability.reset()


# ── paths that end without a row: the marker must fire ─────────────────────

@pytest.mark.asyncio
async def test_an_exception_after_the_broker_call_is_reported_not_swallowed(stub_position_claim, caplog):
    boom = AsyncMock(side_effect=RuntimeError("Error 135: Can't find order with id"))
    with caplog.at_level(logging.INFO):
        res = await _run(_equity_signal(), _broker(equity=boom), record_fill=AsyncMock())

    assert res["result"] == "error"                           # behaviour unchanged
    hits = _marked(caplog)
    assert len(hits) == 1 and hits[0].levelno == logging.CRITICAL
    msg = hits[0].getMessage()
    assert "AMZN" in msg and "stage=exception_after_submit" in msg and "Error 135" in msg
    assert observability.counter("execute.unrecorded_entry") == 1
    # and the handler now writes an ordinary error line too, which it never did
    assert any("Execution error for AMZN" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_fill_whose_row_could_not_be_written_is_reported_with_the_order_id(stub_position_claim, caplog):
    with caplog.at_level(logging.INFO):
        res = await _run(_equity_signal(), _broker(), record_fill=AsyncMock(return_value=None))

    assert res["result"] == "submitted"
    hits = _marked(caplog)
    assert len(hits) == 1
    assert "stage=fill_record_failed" in hits[0].getMessage() and "E-1" in hits[0].getMessage()
    assert observability.counter("execute.unrecorded_entry") == 1


@pytest.mark.asyncio
async def test_a_timeout_whose_pending_row_also_failed_is_reported(stub_position_claim, caplog):
    slow = AsyncMock(side_effect=asyncio.TimeoutError())
    with caplog.at_level(logging.INFO):
        res = await _run(_equity_signal(), _broker(equity=slow), record_fill=AsyncMock(return_value=None))

    assert res["result"] == "pending_confirmation"
    assert "stage=timeout_pending_write_failed" in _marked(caplog)[0].getMessage()
    assert observability.counter("execute.unrecorded_entry") == 1


@pytest.mark.asyncio
async def test_the_options_path_reports_a_failed_row_write_too(stub_position_claim, caplog):
    with caplog.at_level(logging.INFO):
        res = await _run(_options_signal(), _broker(), record_fill=AsyncMock(return_value=None),
                         approved_by="manual")

    assert res["result"] == "submitted"
    hits = _marked(caplog)
    assert len(hits) == 1 and "options" in hits[0].getMessage()
    assert "stage=fill_record_failed" in hits[0].getMessage()


@pytest.mark.asyncio
async def test_an_options_timeout_whose_pending_row_failed_is_reported(stub_position_claim, caplog):
    slow = AsyncMock(side_effect=asyncio.TimeoutError())
    with caplog.at_level(logging.INFO):
        await _run(_options_signal(), _broker(options=slow), record_fill=AsyncMock(return_value=None),
                   approved_by="manual")
    assert "stage=timeout_pending_write_failed" in _marked(caplog)[0].getMessage()


# ── paths that end WITH a row, or with a definite refusal: it must stay silent ──

@pytest.mark.asyncio
async def test_a_recorded_entry_is_not_reported(stub_position_claim, caplog):
    with caplog.at_level(logging.INFO):
        res = await _run(_equity_signal(), _broker(), record_fill=AsyncMock(return_value="trade-1"))
    assert res["result"] == "submitted"
    assert not _marked(caplog)
    assert observability.counter("execute.unrecorded_entry") == 0


@pytest.mark.asyncio
async def test_a_timeout_whose_pending_row_was_written_is_not_reported(stub_position_claim, caplog):
    slow = AsyncMock(side_effect=asyncio.TimeoutError())
    with caplog.at_level(logging.INFO):
        res = await _run(_equity_signal(), _broker(equity=slow),
                         record_fill=AsyncMock(return_value="trade-pending"))
    assert res["result"] == "pending_confirmation"
    assert not _marked(caplog)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["cancelled", "rejected"])
async def test_a_definite_broker_refusal_is_not_reported(stub_position_claim, caplog, status):
    refused = AsyncMock(return_value=MagicMock(order_id="E-9", status=status))
    with caplog.at_level(logging.INFO):
        res = await _run(_equity_signal(), _broker(equity=refused), record_fill=AsyncMock())
    assert res["result"] == "blocked"
    assert not _marked(caplog)
    assert observability.counter("execute.unrecorded_entry") == 0


@pytest.mark.asyncio
async def test_a_failure_before_any_submission_is_not_reported_as_an_unrecorded_entry(stub_position_claim, caplog):
    """The claim is only marked once an order is about to be sent. An error
    earlier than that has nothing to be unrecorded."""
    stub_position_claim.mark_submitted.return_value = False      # claim lost: nothing sent
    with caplog.at_level(logging.INFO):
        res = await _run(_equity_signal(), _broker(), record_fill=AsyncMock())
    assert res["result"] == "skipped"
    assert not _marked(caplog)


@pytest.mark.asyncio
async def test_the_marker_changes_no_result(stub_position_claim):
    """Same dict shape on the failure path as before the marker existed."""
    boom = AsyncMock(side_effect=RuntimeError("x"))
    res = await _run(_equity_signal(), _broker(equity=boom), record_fill=AsyncMock())
    assert set(res) == {"signal_id", "ticker", "asset_type", "result", "error", "executed_at"}


@pytest.mark.asyncio
async def test_an_error_after_the_row_was_recorded_is_not_reported_as_unrecorded(stub_position_claim, caplog):
    """The row exists and the claim is resolved; a later failure (here, the
    event write) is an ordinary error, not an unrecorded entry. Without the
    reset after `resolve`, the outer handler would call every late failure one."""
    real_event = observability.event

    def _flaky(name, **kw):
        if name == "submitted":
            raise RuntimeError("event sink down")
        return real_event(name, **kw)

    with patch.object(observability, "event", side_effect=_flaky), caplog.at_level(logging.INFO):
        res = await _run(_equity_signal(), _broker(), record_fill=AsyncMock(return_value="trade-1"))

    assert res["result"] == "error"
    assert not _marked(caplog)
    assert observability.counter("execute.unrecorded_entry") == 0
