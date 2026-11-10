"""The options watchlist scan must fit its time budget without losing what it found.

Measured 2026-11-10: option-chain requests averaged ~27s against a 60s per-request
timeout and a 240s scheduler limit, only 4 of the IBKR coordinator's 6 workers serve
background (P2) work, requests kept running after their caller gave up, and every
cycle was cancelled at 240s before any candidate was dispatched.

These tests use a fake coordinator that behaves the same way where it matters: a
fixed worker pool, and a request that outlives a caller that stopped waiting.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.main as m

SYMBOLS = [f"S{i}" for i in range(10)]


def _signal(ticker: str, confidence: float = 0.9) -> dict:
    return {"id": f"sig-{ticker}", "ticker": ticker, "asset_type": "options", "strategy": "bull_put_spread",
            "confidence": confidence, "signal_score": confidence, "spread": {"net_credit": 150.0, "max_loss": 350.0}}


class FakeCoordinator:
    """A worker pool of fixed size. A request whose caller times out keeps running (the real
    coordinator shields the job), and a job that never got a worker just waits in the queue."""

    def __init__(self, workers: int):
        self.sem = asyncio.Semaphore(workers)

    async def submit(self, priority, fn, *, timeout=30.0, req_type="generic", symbol=None, **_):
        async def run():
            async with self.sem:
                return await fn()
        task = asyncio.ensure_future(run())
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
        return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    m._options_outstanding.clear()
    m._OPTIONS_STEP_TIMES.clear()
    monkeypatch.setattr(m, "_options_scan_cursor", 0)
    watch = MagicMock()
    watch.get_equity_watchlist.return_value = list(SYMBOLS)
    monkeypatch.setattr(m, "settings", watch)
    yield
    m._options_outstanding.clear()
    m._OPTIONS_STEP_TIMES.clear()


def _budgets(monkeypatch, *, start=5.0, collect=10.0, dispatch=20.0, outstanding=4, request=60.0):
    monkeypatch.setattr(m, "OPTIONS_SCAN_START_BUDGET_S", start)
    monkeypatch.setattr(m, "OPTIONS_SCAN_COLLECT_BUDGET_S", collect)
    monkeypatch.setattr(m, "OPTIONS_SCAN_DISPATCH_BUDGET_S", dispatch)
    monkeypatch.setattr(m, "OPTIONS_SCAN_MAX_OUTSTANDING", outstanding)
    monkeypatch.setattr(m, "OPTIONS_SCAN_REQUEST_TIMEOUT_S", request)


async def _run(scan, *, workers=4, handle=None, settle=0.0):
    """Run one cycle. `settle` keeps the patches in place for that long afterwards, so jobs a
    worker reaches AFTER the cycle returned still call the fake scan (not the real one) and a
    wrongly-run job is visible to the test."""
    handle = handle or AsyncMock()
    with patch("app.main._run_options_scan", new=AsyncMock(side_effect=scan)), \
         patch("app.broker.ibkr_coordinator.ibkr_coordinator", FakeCoordinator(workers)), \
         patch("app.api.routes.trade_desk.handle_signal", new=handle):
        await m._run_options_scan_watchlist()
        if settle:
            await asyncio.sleep(settle)
    return handle


@pytest.mark.asyncio
async def test_the_scan_stops_starting_symbols_at_its_budget_and_the_next_cycle_resumes_there(monkeypatch):
    _budgets(monkeypatch, start=0.25, collect=1.0, outstanding=2)
    order: list[str] = []

    async def scan(symbol, execute=True):
        order.append(symbol)
        await asyncio.sleep(0.1)
        return None

    await _run(scan, workers=2)
    first = list(order)
    cursor = m._options_scan_cursor
    assert 0 < len(first) < len(SYMBOLS), "the budget should have left some symbols unreached"
    assert first == SYMBOLS[:len(first)] and cursor == len(first)

    order.clear()
    await _run(scan, workers=2)
    assert order[0] == SYMBOLS[cursor], "the next cycle must start at the first symbol the last one did not reach"


@pytest.mark.asyncio
async def test_the_cursor_wraps_around_the_end_of_the_list(monkeypatch):
    _budgets(monkeypatch)
    monkeypatch.setattr(m, "_options_scan_cursor", 8)
    order: list[str] = []

    async def scan(symbol, execute=True):
        order.append(symbol)
        return None

    await _run(scan)
    assert sorted(order) == sorted(SYMBOLS)           # every symbol still scanned once
    assert order[0] == SYMBOLS[8]                     # ...starting from the cursor
    assert m._options_scan_cursor == 8                # a full pass lands back where it began


@pytest.mark.asyncio
async def test_outstanding_jobs_never_exceed_the_limit_even_when_every_caller_times_out(monkeypatch):
    """The bug in a caller-side semaphore: it frees the slot when the caller gives up, while the
    job keeps running, so work piles up behind work nobody is waiting for."""
    _budgets(monkeypatch, start=0.6, collect=1.5, outstanding=2, request=0.05)
    running = 0
    peak_running = 0
    peak_outstanding = 0

    async def scan(symbol, execute=True):
        nonlocal running, peak_running, peak_outstanding
        running += 1
        peak_running = max(peak_running, running)
        peak_outstanding = max(peak_outstanding, len(m._options_outstanding))
        try:
            await asyncio.sleep(0.2)          # far longer than the 0.05s request timeout
            return None
        finally:
            running -= 1

    await _run(scan, workers=6, settle=0.6)   # let abandoned jobs finish
    assert peak_outstanding <= 2 and peak_running <= 2
    assert m._options_outstanding == {}, "every job must release its slot when the worker finishes it"


@pytest.mark.asyncio
async def test_a_queued_job_whose_caller_gave_up_is_skipped_instead_of_run_for_nobody(monkeypatch):
    _budgets(monkeypatch, start=2.0, collect=3.0, outstanding=2, request=0.05)
    monkeypatch.setattr(m.settings, "get_equity_watchlist", lambda: ["S0", "S1"])
    ran: list[str] = []

    async def scan(symbol, execute=True):
        ran.append(symbol)
        await asyncio.sleep(0.3)
        return None

    # One worker: S0 runs, S1 waits in the queue and its caller times out after 0.05s.
    await _run(scan, workers=1, settle=0.7)   # S0 finishes; a worker then reaches S1's abandoned job
    assert ran == ["S0"], f"S1's caller had given up, so its job must be skipped, not run: {ran}"
    assert m._options_outstanding == {}


@pytest.mark.asyncio
async def test_a_job_a_worker_reaches_after_the_cycle_ended_is_skipped(monkeypatch):
    _budgets(monkeypatch, start=2.0, collect=0.15, outstanding=4, request=10.0)
    monkeypatch.setattr(m.settings, "get_equity_watchlist", lambda: ["S0", "S1", "S2", "S3"])
    ran: list[str] = []

    async def scan(symbol, execute=True):
        ran.append(symbol)
        await asyncio.sleep(0.4)
        return None

    await _run(scan, workers=1, settle=1.8)   # the cycle ends while S0 runs and S1-S3 are still queued
    assert ran == ["S0"], f"queued jobs of a finished cycle must not run: {ran}"
    assert m._options_outstanding == {}


@pytest.mark.asyncio
async def test_candidates_found_before_the_deadline_are_dispatched_even_though_the_scan_cannot_finish(monkeypatch):
    """The reported failure: the cycle was cancelled at 240s and everything it had found was lost."""
    _budgets(monkeypatch, start=0.3, collect=0.5, outstanding=3)

    async def scan(symbol, execute=True):
        if symbol == "S0":
            await asyncio.sleep(0.05)
            return _signal("S0")
        await asyncio.sleep(1.0)              # slow: cannot finish inside the collect budget
        return _signal(symbol)

    loop = asyncio.get_event_loop()
    t0 = loop.time()
    handle = await _run(scan, workers=3, settle=1.3)   # abandoned slow jobs finish before the patches go
    assert [c.args[0]["ticker"] for c in handle.await_args_list] == ["S0"]
    assert loop.time() - t0 < 2.5


@pytest.mark.asyncio
async def test_dispatch_stops_at_its_own_budget_and_says_how_many_were_left(monkeypatch):
    _budgets(monkeypatch, dispatch=0.0)

    async def scan(symbol, execute=True):
        return _signal(symbol) if symbol in ("S0", "S1") else None

    with patch("app.main.logger") as log:
        handle = await _run(scan)
    handle.assert_not_awaited()
    assert any("not dispatched" in str(c.args[0]) for c in log.warning.call_args_list)


@pytest.mark.asyncio
async def test_the_cycle_logs_how_many_symbols_returned_a_result_in_budget(monkeypatch):
    """The success measure: symbols that returned before the deadline, not jobs that later logged success."""
    _budgets(monkeypatch, start=0.3, collect=0.6, outstanding=2, request=0.1)

    async def scan(symbol, execute=True):
        if symbol in ("S0", "S1"):
            return None
        await asyncio.sleep(1.0)
        return None

    with patch("app.main.logger") as log:
        await _run(scan, workers=2, settle=1.3)
    line = next(c for c in log.info.call_args_list if "returned a result" in c.args[0])
    returned, total = line.args[1], line.args[2]
    assert total == len(SYMBOLS) and returned == 2


@pytest.mark.asyncio
async def test_when_no_symbol_can_start_the_scan_says_so_and_does_not_advance(monkeypatch):
    _budgets(monkeypatch, start=0.1, outstanding=2)
    m._options_acquire()
    m._options_acquire()                      # two jobs from earlier cycles still outstanding
    async def scan(symbol, execute=True):
        return None

    with patch("app.main.logger") as log:
        await _run(scan)
    assert m._options_scan_cursor == 0
    assert any("no symbol could be started" in str(c.args[0]) for c in log.warning.call_args_list)


def test_a_job_outstanding_longer_than_any_real_one_is_dropped_so_it_cannot_block_the_scan(monkeypatch):
    monkeypatch.setattr(m, "OPTIONS_SCAN_STALE_TOKEN_S", 1.0)
    old = m._options_acquire()
    m._options_outstanding[old] -= 10.0       # submitted "10s ago"
    fresh = m._options_acquire()
    with patch("app.main.logger") as log:
        assert m._options_in_flight() == 1
    assert fresh in m._options_outstanding and old not in m._options_outstanding
    assert log.warning.called


def test_step_timing_is_a_noop_unless_the_watchlist_scan_started_timing_the_symbol():
    m._options_step("NOPE", "price_bars")
    assert "NOPE" not in m._OPTIONS_STEP_TIMES
    m._log_options_step_times("NOPE")         # nothing recorded: must not raise


def test_a_slow_symbol_logs_its_steps_at_info_and_a_fast_one_does_not():
    import time as _t
    now = _t.monotonic()
    m._OPTIONS_STEP_TIMES["AMD"] = {"start": now - 70.0, "last": now - 70.0, "steps": {}}
    m._OPTIONS_STEP_TIMES["AMD"]["steps"] = {"live_chain_quote": 61.2, "ai_scorer": 0.4}
    with patch("app.main.logger") as log:
        m._log_options_step_times("AMD")
        m._OPTIONS_STEP_TIMES["AEP"] = {"start": now - 1.0, "last": now - 1.0, "steps": {"price_bars": 1.0}}
        m._log_options_step_times("AEP")
    info = log.info.call_args
    assert info.args[1] == "AMD" and "live_chain_quote=61.2s" in info.args[3]
    assert log.debug.called and log.debug.call_args.args[1] == "AEP"
    assert "AMD" not in m._OPTIONS_STEP_TIMES and "AEP" not in m._OPTIONS_STEP_TIMES
