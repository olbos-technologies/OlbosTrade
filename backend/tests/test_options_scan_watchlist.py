"""
Tests for _run_options_scan_watchlist — the options scanner's widened
universe (full equity watchlist instead of hardcoded SPY/QQQ), and its
rank-then-execute-best-first ordering (mirrors _run_equity_scan's pattern).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


def _signal(ticker: str, confidence: float) -> dict:
    return {
        "id": f"sig-{ticker}",
        "ticker": ticker,
        "asset_type": "options",
        "strategy": "bull_put_spread",
        "confidence": confidence,
        "signal_score": confidence,
        "spread": {"net_credit": 150.0, "max_loss": 350.0},
    }


@pytest.mark.asyncio
async def test_scans_every_watchlist_symbol_with_execute_false():
    """Every symbol in the watchlist must be scanned build-only (execute=False)
    — execution only happens afterward, on the ranked winners."""
    from app.core.config import settings
    from app.main import _run_options_scan_watchlist

    watchlist = settings.get_equity_watchlist()

    with patch("app.main._run_options_scan", new=AsyncMock(return_value=None)) as mock_scan, \
         patch("app.api.routes.trade_desk.handle_signal", new=AsyncMock()) as mock_handle:
        await _run_options_scan_watchlist()

    assert mock_scan.await_count == len(watchlist)
    for call in mock_scan.await_args_list:
        args, kwargs = call.args, call.kwargs
        symbol = args[0] if args else kwargs.get("symbol")
        assert symbol in watchlist
        assert kwargs.get("execute") is False
    mock_handle.assert_not_awaited()  # nothing qualified — no execution


@pytest.mark.asyncio
async def test_ranks_and_executes_candidates_highest_first():
    """When multiple symbols qualify in one cycle, the highest-quality
    candidate must be routed to execution first, not whichever symbol
    happened to be scanned first."""
    from app.core.config import settings
    from app.main import _run_options_scan_watchlist

    watchlist = settings.get_equity_watchlist()
    assert len(watchlist) >= 2
    weak_symbol, strong_symbol = watchlist[0], watchlist[1]

    async def _fake_scan(symbol, execute=True):
        if symbol == weak_symbol:
            return _signal(weak_symbol, confidence=0.30)
        if symbol == strong_symbol:
            return _signal(strong_symbol, confidence=0.95)
        return None

    with patch("app.main._run_options_scan", new=AsyncMock(side_effect=_fake_scan)), \
         patch("app.api.routes.trade_desk.handle_signal", new=AsyncMock()) as mock_handle:
        await _run_options_scan_watchlist()

    assert mock_handle.await_count == 2
    executed_order = [call.args[0]["ticker"] for call in mock_handle.await_args_list]
    assert executed_order[0] == strong_symbol, (
        f"expected {strong_symbol} (higher confidence) to execute before "
        f"{weak_symbol}, got order {executed_order}"
    )


@pytest.mark.asyncio
async def test_no_candidates_skips_execution_entirely():
    from app.main import _run_options_scan_watchlist

    with patch("app.main._run_options_scan", new=AsyncMock(return_value=None)), \
         patch("app.api.routes.trade_desk.handle_signal", new=AsyncMock()) as mock_handle:
        await _run_options_scan_watchlist()

    mock_handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_symbol_failing_does_not_block_others():
    from app.core.config import settings
    from app.main import _run_options_scan_watchlist

    watchlist = settings.get_equity_watchlist()
    failing_symbol, ok_symbol = watchlist[0], watchlist[1]

    async def _fake_scan(symbol, execute=True):
        if symbol == failing_symbol:
            raise RuntimeError("yfinance down")
        if symbol == ok_symbol:
            return _signal(ok_symbol, confidence=0.80)
        return None

    with patch("app.main._run_options_scan", new=AsyncMock(side_effect=_fake_scan)), \
         patch("app.api.routes.trade_desk.handle_signal", new=AsyncMock()) as mock_handle:
        await _run_options_scan_watchlist()

    mock_handle.assert_awaited_once()
    assert mock_handle.await_args.args[0]["ticker"] == ok_symbol


# ── progress notes (observability only; behavior is covered by the tests above) ──

@pytest.mark.asyncio
async def test_the_scan_records_how_far_it_got_so_a_timeout_can_say_where():
    import app.main as m
    from app.core.config import settings

    watchlist = settings.get_equity_watchlist()
    seen_during_dispatch = []

    async def _fake_scan(symbol, execute=True):
        return _signal(symbol, 0.9) if symbol == watchlist[0] else None

    async def _fake_dispatch(sig):
        seen_during_dispatch.append(m._scan_progress.get("options_scan"))

    m._scan_progress.pop("options_scan", None)
    with patch("app.main._run_options_scan", new=AsyncMock(side_effect=_fake_scan)), \
         patch("app.services.entry_dispatch.dispatch_entry", new=_fake_dispatch):
        await m._run_options_scan_watchlist()

    # While dispatching, the note names the stage, the position and the ticker...
    assert seen_during_dispatch and seen_during_dispatch[0].startswith(f"dispatching 1/1 ({watchlist[0]})")
    # ...and it is still there afterwards, so a timeout during dispatch is attributed to dispatch.
    assert m._scan_progress["options_scan"].startswith("dispatching 1/1")


@pytest.mark.asyncio
async def test_a_scan_with_no_candidates_leaves_a_note_that_it_finished_scanning():
    import app.main as m
    from app.core.config import settings

    total = len(settings.get_equity_watchlist())
    m._scan_progress.pop("options_scan", None)
    with patch("app.main._run_options_scan", new=AsyncMock(return_value=None)):
        await m._run_options_scan_watchlist()
    assert m._scan_progress["options_scan"].startswith(f"scanned {total}/{total} symbols (0 failed)")


@pytest.mark.asyncio
async def test_failures_are_counted_in_the_note_and_the_scan_still_completes():
    import app.main as m
    from app.core.config import settings

    watchlist = settings.get_equity_watchlist()

    async def _fake_scan(symbol, execute=True):
        if symbol in watchlist[:3]:
            raise RuntimeError("chain fetch failed")
        return None

    m._scan_progress.pop("options_scan", None)
    with patch("app.main._run_options_scan", new=AsyncMock(side_effect=_fake_scan)):
        await m._run_options_scan_watchlist()
    assert f"scanned {len(watchlist)}/{len(watchlist)} symbols (3 failed)" in m._scan_progress["options_scan"]


@pytest.mark.asyncio
async def test_the_scheduler_timeout_message_now_names_where_the_options_scan_was():
    """The reported line was 'timed out after 240s - skipped (last progress: unknown)'."""
    import asyncio
    import app.main as m

    async def _slow_scan():
        m._note_progress("options_scan", "scanned 40/102 symbols (2 failed), 200s in")
        await asyncio.sleep(10)

    with patch("app.main.logger") as log:
        await m._guarded(_slow_scan(), "options_scan", 0.05)
    args = log.error.call_args.args
    assert "timed out" in args[0] and "scanned 40/102 symbols (2 failed), 200s in" in args
