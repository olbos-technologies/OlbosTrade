"""An entry must not be lost because the scan that started it was cut off.

Written after the 90 s equity_scan guard was found able to cancel an entry that
was already waiting on the broker: the order stayed live, the trade row and
execution log were never written, and the claim stayed `submitted` - the shape
of a position later labelled "adopted_untracked".

Pinned here:
  * dispatch_entry runs the entry to completion even if its caller is cancelled;
  * an entry's own failure is logged, not lost, when nobody is waiting;
  * an entry cancelled AFTER the order was sent leaves an unknown claim and a
    pending row, logs the execution, and still propagates the cancellation;
  * an entry cancelled BEFORE the order was sent leaves nothing behind;
  * a scan timeout says how far the scan got.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.api.routes import trade_desk
from app.services import entry_dispatch, position_claim


def _run(coro):
    return asyncio.run(coro)


# ── dispatch ───────────────────────────────────────────────────────────────

def test_the_entry_finishes_when_the_waiting_scan_is_cancelled():
    finished = []

    async def slow_entry(signal):
        await asyncio.sleep(0.05)
        finished.append(signal["ticker"])

    async def go():
        with patch.object(trade_desk, "handle_signal", slow_entry):
            waiter = asyncio.ensure_future(entry_dispatch.dispatch_entry({"ticker": "FER"}))
            await asyncio.sleep(0.01)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert finished == [] and entry_dispatch.entries_in_flight() == 1
            await asyncio.sleep(0.1)
        return finished

    assert _run(go()) == ["FER"]
    assert entry_dispatch.entries_in_flight() == 0


def test_without_cancellation_it_behaves_like_a_plain_await():
    async def ok(signal):
        signal["done"] = True

    async def boom(signal):
        raise ValueError("nope")

    async def go():
        sig = {"ticker": "A"}
        with patch.object(trade_desk, "handle_signal", ok):
            await entry_dispatch.dispatch_entry(sig)
        assert sig["done"] is True
        with patch.object(trade_desk, "handle_signal", boom), pytest.raises(ValueError):
            await entry_dispatch.dispatch_entry({"ticker": "B"})

    _run(go())


def test_a_failure_nobody_is_waiting_for_is_still_logged(caplog):
    async def boom(signal):
        await asyncio.sleep(0.03)
        raise RuntimeError("broker exploded")

    async def go():
        with patch.object(trade_desk, "handle_signal", boom):
            waiter = asyncio.ensure_future(entry_dispatch.dispatch_entry({"ticker": "ZZZ"}))
            await asyncio.sleep(0.01)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            await asyncio.sleep(0.1)

    with caplog.at_level(logging.WARNING):
        _run(go())
    text = caplog.text
    assert "Entry for ZZZ failed: broker exploded" in text
    assert "scan was cancelled while the entry for ZZZ was in flight" in text


# ── the entry itself ───────────────────────────────────────────────────────

_ROW = dict(organization_id="o", strategy="equity", underlying="FER", status="pending",
            dispatch_id="sig-1")


def _settle(row, *, record=True, mark_raises=None):
    mark = AsyncMock(side_effect=mark_raises)
    rec = AsyncMock(return_value=object() if record else None)
    log = AsyncMock()
    with patch.object(position_claim, "mark_unknown", mark), \
         patch("app.services.trade_recorder.trade_recorder.record_fill", rec), \
         patch.object(trade_desk, "_log_execution", log), \
         patch.object(trade_desk, "_report_unrecorded_entry") as report:
        _run(trade_desk._settle_interrupted_entry(
            ticker="FER", asset_type="equity", signal={"id": "sig-1"},
            claim=object(), row=row))
    return mark, rec, log, report


def test_settling_keeps_the_claim_writes_the_row_and_logs_the_execution():
    mark, rec, log, report = _settle(dict(_ROW))
    mark.assert_awaited_once()
    rec.assert_awaited_once_with(**_ROW)
    assert log.await_args.args[0]["result"] == "interrupted"
    report.assert_not_called()


def test_a_row_that_cannot_be_written_is_reported_loudly():
    _, rec, log, report = _settle(dict(_ROW), record=False)
    assert report.call_args.kwargs["stage"] == "cancelled_after_submit"
    log.assert_awaited_once()


def test_a_settle_that_itself_fails_still_reports_and_logs():
    _, _, log, report = _settle(dict(_ROW), mark_raises=RuntimeError("db down"))
    assert "db down" in report.call_args.kwargs["error"]
    log.assert_awaited_once()


def test_nothing_to_write_is_reported_not_silently_dropped():
    _, rec, _, report = _settle(None)
    rec.assert_not_awaited()
    assert report.call_args.kwargs["stage"] == "cancelled_after_submit"


def test_the_settle_cannot_hang_the_cancellation(monkeypatch):
    monkeypatch.setattr(trade_desk, "_SETTLE_BUDGET_SECONDS", 0.05)

    async def hang(*a, **k):
        await asyncio.sleep(5)

    log = AsyncMock()
    with patch.object(position_claim, "mark_unknown", hang), \
         patch.object(trade_desk, "_log_execution", log), \
         patch.object(trade_desk, "_report_unrecorded_entry") as report:
        _run(trade_desk._settle_interrupted_entry(
            ticker="FER", asset_type="equity", signal={}, claim=object(), row=dict(_ROW)))
    assert "TimeoutError" in report.call_args.kwargs["error"]


# ── through _execute_signal, equity and options ───────────────────────────

from test_unrecorded_entry_marker import (  # noqa: E402
    _broker, _equity_signal, _market_open, _options_signal, _run as _drive,
)


async def _cancelled_entry(signal, broker, record_fill, log):
    with patch.object(trade_desk, "_log_execution", log):
        return await _drive(signal, broker, record_fill=record_fill, approved_by="manual")


@pytest.mark.asyncio
async def test_an_equity_entry_cancelled_after_the_send_is_settled_and_still_cancelled(stub_position_claim):
    cancelled = AsyncMock(side_effect=asyncio.CancelledError())
    record, log = AsyncMock(return_value=object()), AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        await _cancelled_entry(_equity_signal(), _broker(equity=cancelled), record, log)

    stub_position_claim.mark_unknown.assert_awaited_once()
    assert "cancelled after submit" in stub_position_claim.mark_unknown.await_args.args[1]
    kw = record.await_args.kwargs
    assert (kw["underlying"], kw["status"], kw["quantity"], kw["option_type"]) == \
        ("AMZN", "pending", 7, "equity_long")
    assert kw["dispatch_id"] == "sig-marker"
    assert log.await_args.args[0]["result"] == "interrupted"
    stub_position_claim.resolve.assert_not_awaited()      # never released on a guess


@pytest.mark.asyncio
async def test_an_options_entry_cancelled_after_the_send_is_settled_too(stub_position_claim):
    cancelled = AsyncMock(side_effect=asyncio.CancelledError())
    record, log = AsyncMock(return_value=object()), AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        await _cancelled_entry(_options_signal(), _broker(options=cancelled), record, log)

    stub_position_claim.mark_unknown.assert_awaited_once()
    kw = record.await_args.kwargs
    assert (kw["underlying"], kw["status"], kw["strategy"]) == ("SPY", "pending", "bull_put_spread")
    assert log.await_args.args[0]["result"] == "interrupted"


@pytest.mark.asyncio
async def test_cancelled_with_no_row_written_it_is_reported_unrecorded(stub_position_claim, caplog):
    cancelled = AsyncMock(side_effect=asyncio.CancelledError())
    with caplog.at_level(logging.INFO), pytest.raises(asyncio.CancelledError):
        await _cancelled_entry(_equity_signal(), _broker(equity=cancelled),
                               AsyncMock(return_value=None), AsyncMock())
    assert any("ENTRY_UNRECORDED" in r.getMessage() and "cancelled_after_submit" in r.getMessage()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_cancelled_before_anything_was_sent_leaves_nothing_behind(stub_position_claim):
    """Cancellation while the entry is still being checked must not invent a row
    or park the claim: nothing has reached the broker."""
    record, log = AsyncMock(), AsyncMock()
    stub_position_claim.mark_submitted.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await _cancelled_entry(_equity_signal(), _broker(), record, log)
    stub_position_claim.mark_unknown.assert_not_awaited()
    record.assert_not_awaited()
    log.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_completed_entry_is_not_settled_as_interrupted(stub_position_claim):
    record, log = AsyncMock(return_value=object()), AsyncMock()
    res = await _cancelled_entry(_equity_signal(), _broker(), record, log)
    assert res["result"] == "submitted"
    record.assert_awaited_once()                          # the normal row only
    stub_position_claim.mark_unknown.assert_not_awaited()


# ── the timeout log ────────────────────────────────────────────────────────

def test_a_scan_timeout_says_how_far_it_got(caplog):
    from app import main

    async def slow():
        main._note_progress("equity_scan", "placing entries 2/3 (FER)")
        await asyncio.sleep(1)

    with caplog.at_level(logging.ERROR):
        _run(main._guarded(slow(), "equity_scan", 0.05))
    assert "Scheduler task 'equity_scan' timed out after 0s" in caplog.text
    assert "last progress: placing entries 2/3 (FER)" in caplog.text
