"""Tests for signal_outcome_tracker — signal persistence + forward resolution."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from sqlalchemy.exc import InvalidRequestError

from app.services.equity_signal_engine import EQUITY_SCORING_VERSION
from app.services.signal_outcome_tracker import (
    _resolve_one,
    check_pending_outcomes,
    compute_signal_outcome_stats,
    record_signal,
)


def _bars(rows: list[tuple[str, float, float, float]]) -> pd.DataFrame:
    """rows of (date_str, high, low, close) -> a yfinance-shaped DataFrame."""
    idx = pd.to_datetime([r[0] for r in rows]).tz_localize("America/New_York")
    return pd.DataFrame(
        {
            "High":  [r[1] for r in rows],
            "Low":   [r[2] for r in rows],
            "Close": [r[3] for r in rows],
        },
        index=idx,
    )


def _row(action="BUY", entry=100.0, stop=96.0, target=108.0, generated="2026-01-05"):
    return SimpleNamespace(
        generated_at=datetime.fromisoformat(generated).replace(tzinfo=timezone.utc),
        action=action,
        entry_price=entry,
        stop_price=stop,
        target_price=target,
    )


def _mock_session(existing_id=None):
    """
    A mocked AsyncSession for record_signal().

    `existing_id` is what the dedup lookup finds: None means no signal has been
    recorded for this (ticker, action, day) yet, so the insert should proceed.

    THIS FAKE AUTOBEGINS, and that is the whole point of it.

    The previous version handed back an unconditional MagicMock for begin(),
    which never raised no matter how many times it was called. A real
    AsyncSession does not behave that way: the first operation — including a
    SELECT — implicitly begins the transaction, and calling begin() after that
    raises InvalidRequestError.

    record_signal did exactly that, so in production it failed on EVERY
    routable BUY/SELL signal with "A transaction is already begun on this
    Session" and nothing was written to signal_outcomes from #45 onward. Every
    test here passed throughout, because the fake was more permissive than the
    database. A fake that cannot reproduce the failure certifies the bug.
    """
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    state = {"in_transaction": False}

    def _begin():
        if state["in_transaction"]:
            raise InvalidRequestError(
                "A transaction is already begun on this Session."
            )
        state["in_transaction"] = True
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    async def _execute(*_a, **_k):
        state["in_transaction"] = True        # autobegin, as SQLAlchemy does
        lookup = MagicMock()
        lookup.scalar_one_or_none = MagicMock(return_value=existing_id)
        return lookup

    async def _commit(*_a, **_k):
        state["in_transaction"] = False

    session.begin = MagicMock(side_effect=_begin)
    session.execute = AsyncMock(side_effect=_execute)
    session.commit = AsyncMock(side_effect=_commit)
    session.add = MagicMock()
    return session


# ── record_signal ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_signal_noop_for_hold():
    result = await record_signal({"action": "HOLD", "ticker": "AAPL"})
    assert result is None


@pytest.mark.asyncio
async def test_record_signal_noop_without_trade_plan():
    result = await record_signal({"action": "BUY", "ticker": "AAPL", "trade_plan": {}})
    assert result is None


@pytest.mark.asyncio
async def test_record_signal_inserts_row():
    session = _mock_session()

    signal = {
        "id": "sig-1", "ticker": "NVDA", "action": "BUY", "confidence": 0.71,
        "generated_at": "2026-01-05T14:30:00+00:00",
        "trade_plan": {"entry_price": 100.0, "stop_price": 96.0, "target_price": 108.0,
                        "target_move_pct": 8.0},
        "indicators": {"rsi": 55.0, "macd": 0.5, "bb_pct_b": 0.6, "atr": 2.0, "volume_ratio": 1.2},
    }
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        result = await record_signal(signal)

    assert result is not None
    assert session.add.call_count == 1
    inserted = session.add.call_args.args[0]
    assert inserted.ticker == "NVDA"
    assert inserted.status == "pending"
    assert float(inserted.entry_price) == 100.0


@pytest.mark.asyncio
async def test_record_signal_stamps_regime_and_engine_version():
    session = _mock_session()

    signal = {
        "id": "sig-2", "ticker": "AMD", "action": "SELL", "confidence": 0.75,
        "regime": "low_vol_trending",
        "generated_at": "2026-01-05T14:30:00+00:00",
        "trade_plan": {"entry_price": 50.0, "stop_price": 52.0, "target_price": 44.0},
    }
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        await record_signal(signal)

    inserted = session.add.call_args.args[0]
    assert inserted.regime == "low_vol_trending"
    assert inserted.signal_engine_version == EQUITY_SCORING_VERSION


@pytest.mark.asyncio
async def test_record_signal_regime_none_when_absent_from_signal():
    session = _mock_session()

    signal = {
        "id": "sig-3", "ticker": "MSFT", "action": "BUY", "confidence": 0.75,
        "generated_at": "2026-01-05T14:30:00+00:00",
        "trade_plan": {"entry_price": 100.0, "stop_price": 96.0, "target_price": 108.0},
    }
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        await record_signal(signal)

    inserted = session.add.call_args.args[0]
    assert inserted.regime is None
    # signal_engine_version is stamped unconditionally, regardless of regime.
    assert inserted.signal_engine_version == EQUITY_SCORING_VERSION


@pytest.mark.asyncio
async def test_record_signal_skips_duplicate_same_ticker_action_day():
    """The scanner re-emits the same setup every tick — only the first is kept."""
    session = _mock_session(existing_id="already-there")
    signal = {
        "id": "sig-dupe", "ticker": "NVDA", "action": "BUY", "confidence": 0.71,
        "generated_at": "2026-01-05T19:45:00+00:00",
        "trade_plan": {"entry_price": 101.0, "stop_price": 97.0, "target_price": 109.0},
    }
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        result = await record_signal(signal)

    session.add.assert_not_called()
    # Returns the row that already exists rather than None, so a caller can
    # still tie the re-emission back to the tracked signal.
    assert result == "already-there"


@pytest.mark.asyncio
async def test_record_signal_dedup_window_is_one_utc_day():
    """The lookup must bound a single UTC day, not scan the whole table."""
    session = _mock_session()
    signal = {
        "id": "sig-window", "ticker": "AMD", "action": "BUY", "confidence": 0.7,
        "generated_at": "2026-01-05T23:30:00+00:00",
        "trade_plan": {"entry_price": 100.0, "stop_price": 96.0, "target_price": 108.0},
    }
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        await record_signal(signal)

    # Render the emitted SELECT and confirm it bounds generated_at on both sides.
    stmt = str(session.execute.call_args.args[0])
    assert "generated_at >=" in stmt and "generated_at <" in stmt
    assert "ticker" in stmt and "action" in stmt
    session.add.assert_called_once()


@pytest.mark.asyncio
async def test_record_signal_naive_generated_at_treated_as_utc():
    """An offset-less timestamp must not land the dedup window on the wrong day."""
    session = _mock_session()
    signal = {
        "id": "sig-naive", "ticker": "MSFT", "action": "BUY", "confidence": 0.7,
        "generated_at": "2026-01-05T00:30:00",          # no offset
        "trade_plan": {"entry_price": 100.0, "stop_price": 96.0, "target_price": 108.0},
    }
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        await record_signal(signal)

    inserted = session.add.call_args.args[0]
    assert inserted.generated_at.tzinfo is not None
    assert inserted.generated_at.date().isoformat() == "2026-01-05"


@pytest.mark.asyncio
async def test_record_signal_different_action_same_day_is_not_a_duplicate():
    """BUY and SELL on one ticker are distinct signals, not duplicates."""
    session = _mock_session()          # lookup finds nothing for this action
    signal = {
        "id": "sig-sell", "ticker": "NVDA", "action": "SELL", "confidence": 0.7,
        "generated_at": "2026-01-05T19:45:00+00:00",
        "trade_plan": {"entry_price": 100.0, "stop_price": 104.0, "target_price": 92.0},
    }
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        result = await record_signal(signal)

    session.add.assert_called_once()
    assert result is not None


@pytest.mark.asyncio
async def test_record_signal_returns_none_on_db_failure():
    with patch("app.core.database.AsyncSessionLocal", side_effect=Exception("db down")):
        result = await record_signal({
            "action": "BUY", "ticker": "X",
            "trade_plan": {"entry_price": 1, "stop_price": 0.9, "target_price": 1.1},
        })
    assert result is None


# ── _resolve_one ─────────────────────────────────────────────────────────────

def test_resolve_one_buy_hits_target():
    row = _row(action="BUY", entry=100, stop=96, target=108)
    hist = _bars([
        ("2026-01-06", 102, 99, 101),
        ("2026-01-07", 109, 100, 108.5),   # target crossed
    ])
    status, exit_price, resolved_at, days, mfe, mae, *_exc = _resolve_one(row, hist, max_hold_days=20)
    assert status == "target_hit"
    assert exit_price == 108
    assert days == 2


# _resolve_one returns nine values since the MFE-uncensoring fix: the six
# resolution values asserted here, plus the uncensored excursions and the bars
# actually observed. `*_exc` keeps these tests focused on resolution — which
# deliberately did NOT change — while the new half is covered in
# test_mfe_is_not_censored_at_the_target.py.
def test_resolve_one_buy_hits_stop():
    row = _row(action="BUY", entry=100, stop=96, target=108)
    hist = _bars([
        ("2026-01-06", 101, 95, 96.5),   # stop crossed
    ])
    status, exit_price, resolved_at, days, mfe, mae, *_exc = _resolve_one(row, hist, max_hold_days=20)
    assert status == "stop_hit"
    assert exit_price == 96
    assert days == 1


def test_resolve_one_same_day_conflict_stop_wins():
    """Both target and stop crossed on the same bar — conservative tie-break."""
    row = _row(action="BUY", entry=100, stop=96, target=108)
    hist = _bars([
        ("2026-01-06", 110, 95, 100),   # both crossed intraday
    ])
    status, *_ = _resolve_one(row, hist, max_hold_days=20)
    assert status == "stop_hit"


def test_resolve_one_sell_hits_target():
    row = _row(action="SELL", entry=100, stop=104, target=92)
    hist = _bars([
        ("2026-01-06", 101, 91, 92.5),   # low crossed target for a short
    ])
    status, exit_price, *_ = _resolve_one(row, hist, max_hold_days=20)
    assert status == "target_hit"
    assert exit_price == 92


def test_resolve_one_sell_hits_stop():
    row = _row(action="SELL", entry=100, stop=104, target=92)
    hist = _bars([
        ("2026-01-06", 105, 99, 104.5),   # high crossed stop for a short
    ])
    status, exit_price, *_ = _resolve_one(row, hist, max_hold_days=20)
    assert status == "stop_hit"
    assert exit_price == 104


def test_resolve_one_still_pending_within_available_bars():
    row = _row(action="BUY", entry=100, stop=96, target=108)
    hist = _bars([("2026-01-06", 101, 99, 100.5)])
    assert _resolve_one(row, hist, max_hold_days=20) is None


def test_resolve_one_expires_after_max_hold_days():
    row = _row(action="BUY", entry=100, stop=96, target=108, generated="2026-01-01")
    # 3 quiet bars, never touching target or stop, max_hold_days=3
    hist = _bars([
        ("2026-01-02", 101, 99, 100.5),
        ("2026-01-03", 102, 99, 101.0),
        ("2026-01-04", 102, 100, 101.5),
    ])
    status, exit_price, resolved_at, days, mfe, mae, *_exc = _resolve_one(row, hist, max_hold_days=3)
    assert status == "expired"
    assert exit_price == 101.5
    assert days == 3


def test_resolve_one_ignores_bars_on_or_before_entry_date():
    row = _row(action="BUY", entry=100, stop=96, target=108, generated="2026-01-05")
    hist = _bars([
        ("2026-01-05", 200, 1, 100),   # same day as entry — must be skipped
        ("2026-01-06", 101, 99, 100.5),
    ])
    # If the entry-day bar were counted, this would resolve immediately
    # (its wild high/low would trip both target and stop). It must not.
    assert _resolve_one(row, hist, max_hold_days=20) is None


def test_resolve_one_tracks_mfe_mae():
    row = _row(action="BUY", entry=100, stop=90, target=120)
    hist = _bars([
        ("2026-01-06", 105, 98, 104),    # +5% fav, -2% adv
        ("2026-01-07", 103, 97, 102),    # +3% fav, -3% adv (mfe/mae unchanged from day 1)
    ])
    # Neither target (120) nor stop (90) is ever crossed — force resolution
    # via expiry on day 2 so mfe/mae can be read from the return value.
    status, _, _, _, mfe, mae, *_exc = _resolve_one(row, hist, max_hold_days=2)
    assert status == "expired"
    assert mfe == pytest.approx(0.05)
    assert mae == pytest.approx(-0.03)


# ── check_pending_outcomes ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_pending_outcomes_no_pending_rows():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [])))

    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        summary = await check_pending_outcomes()

    # Subset rather than exact equality: the summary also carries coverage
    # fields (tickers_covered/truncated/...) so a partial pass can be told
    # apart from a complete one. This test is about the counts being zero,
    # not about the dict's exact shape.
    assert {k: summary[k] for k in
            ("checked", "target_hit", "stop_hit", "expired", "still_pending")} == \
        {"checked": 0, "target_hit": 0, "stop_hit": 0, "expired": 0, "still_pending": 0}
    assert summary["truncated"] is False


@pytest.mark.asyncio
async def test_check_pending_outcomes_resolves_and_updates_row():
    pending_row = _row(action="BUY", entry=100, stop=96, target=108, generated="2026-01-05")
    pending_row.id = "row-1"
    pending_row.ticker = "AAPL"

    query_session = AsyncMock()
    query_session.__aenter__ = AsyncMock(return_value=query_session)
    query_session.__aexit__ = AsyncMock(return_value=False)
    query_session.execute = AsyncMock(
        return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [pending_row]))
    )

    # Resolutions are written as a batched executemany keyed on the primary
    # key, not by mutating an ORM object fetched per row — so this asserts on
    # the payload that actually reaches the database. Checking a mutated
    # MagicMock attribute would only re-describe the old implementation's
    # mechanism, which is exactly what made a stalled job invisible.
    written: list = []

    update_session = AsyncMock()
    update_session.__aenter__ = AsyncMock(return_value=update_session)
    update_session.__aexit__ = AsyncMock(return_value=False)
    begin = AsyncMock()
    begin.__aenter__ = AsyncMock(return_value=update_session)
    begin.__aexit__ = AsyncMock(return_value=False)
    update_session.begin = MagicMock(return_value=begin)

    async def _capture(stmt, params=None):
        if params:
            written.extend(params if isinstance(params, list) else [params])
        return MagicMock()

    update_session.execute = AsyncMock(side_effect=_capture)

    sessions = [query_session, update_session]

    def _factory():
        return sessions.pop(0) if sessions else update_session

    hist = _bars([("2026-01-07", 109, 100, 108.5)])

    with patch("app.core.database.AsyncSessionLocal", side_effect=_factory), \
         patch("app.services.signal_outcome_tracker._fetch_daily_bars", AsyncMock(return_value=hist)):
        summary = await check_pending_outcomes()

    assert summary["checked"] == 1
    assert summary["target_hit"] == 1
    assert summary["tickers_covered"] == 1 and summary["truncated"] is False

    payloads = [w for w in written if isinstance(w, dict) and "status" in w]
    assert len(payloads) == 1, f"expected one resolution payload, got {payloads}"
    assert payloads[0]["id"] == "row-1"
    assert payloads[0]["status"] == "target_hit"


@pytest.mark.asyncio
async def test_check_pending_outcomes_survives_bars_fetch_failure():
    pending_row = _row(generated="2026-01-05")
    pending_row.id = "row-1"
    pending_row.ticker = "BROKEN"

    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(
        return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [pending_row]))
    )

    with patch("app.core.database.AsyncSessionLocal", return_value=session), \
         patch("app.services.signal_outcome_tracker._fetch_daily_bars",
               AsyncMock(side_effect=Exception("network down"))):
        summary = await check_pending_outcomes()

    # The failing ticker is skipped entirely — not counted as checked.
    assert summary["checked"] == 0


# ── compute_signal_outcome_stats ─────────────────────────────────────────────

def test_compute_stats_empty():
    stats = compute_signal_outcome_stats([])
    assert stats["total"] == 0
    assert stats["hit_rate"] is None


def test_compute_stats_hit_rate_excludes_expired_and_pending():
    outcomes = [
        {"ticker": "A", "status": "target_hit", "confidence": 0.8, "days_to_resolve": 3},
        {"ticker": "B", "status": "target_hit", "confidence": 0.8, "days_to_resolve": 5},
        {"ticker": "C", "status": "stop_hit",   "confidence": 0.7, "days_to_resolve": 2},
        {"ticker": "D", "status": "expired",    "confidence": 0.6, "days_to_resolve": 20},
        {"ticker": "E", "status": "pending",    "confidence": 0.9, "days_to_resolve": None},
    ]
    stats = compute_signal_outcome_stats(outcomes)
    assert stats["total"] == 5
    assert stats["target_hit"] == 2
    assert stats["stop_hit"] == 1
    assert stats["expired"] == 1
    assert stats["pending"] == 1
    assert stats["hit_rate"] == pytest.approx(2 / 3, abs=0.001)
    assert stats["avg_days_to_target"] == pytest.approx(4.0)
    assert stats["avg_days_to_stop"] == pytest.approx(2.0)


def test_compute_stats_confidence_bucket_boundaries():
    outcomes = [
        {"ticker": "A", "status": "target_hit", "confidence": 0.64, "days_to_resolve": 1},
        {"ticker": "B", "status": "target_hit", "confidence": 0.82, "days_to_resolve": 1},
    ]
    stats = compute_signal_outcome_stats(outcomes)
    assert stats["by_confidence_bucket"]["0.00-0.65"]["count"] == 1
    assert stats["by_confidence_bucket"]["0.80-1.00"]["count"] == 1


def test_compute_stats_by_regime_groups_correctly():
    outcomes = [
        {"ticker": "A", "status": "target_hit", "confidence": 0.82, "days_to_resolve": 1,
         "regime": "low_vol_trending"},
        {"ticker": "B", "status": "stop_hit", "confidence": 0.71, "days_to_resolve": 2,
         "regime": "low_vol_trending"},
        {"ticker": "C", "status": "target_hit", "confidence": 0.90, "days_to_resolve": 1,
         "regime": "high_vol"},
    ]
    stats = compute_signal_outcome_stats(outcomes)
    assert set(stats["by_regime"].keys()) == {"low_vol_trending", "high_vol"}
    # Same bucket shape as the top-level by_confidence_bucket, scoped to
    # just that regime's outcomes.
    assert stats["by_regime"]["low_vol_trending"]["0.80-1.00"]["count"] == 1
    assert stats["by_regime"]["low_vol_trending"]["0.70-0.75"]["count"] == 1
    assert stats["by_regime"]["high_vol"]["0.80-1.00"]["count"] == 1
    assert stats["by_regime"]["high_vol"]["0.70-0.75"]["count"] == 0


def test_compute_stats_by_regime_excludes_missing_regime():
    outcomes = [
        {"ticker": "A", "status": "target_hit", "confidence": 0.82, "days_to_resolve": 1,
         "regime": "low_vol_trending"},
        {"ticker": "B", "status": "target_hit", "confidence": 0.82, "days_to_resolve": 1},
        {"ticker": "C", "status": "target_hit", "confidence": 0.82, "days_to_resolve": 1,
         "regime": None},
    ]
    stats = compute_signal_outcome_stats(outcomes)
    # Missing/None regime never becomes a spurious bucket key — those rows
    # still count in by_confidence_bucket/by_ticker, just not by_regime.
    assert set(stats["by_regime"].keys()) == {"low_vol_trending"}
    assert stats["total"] == 3


# ── Wiring into the equity scanner ───────────────────────────────────────────

def test_record_signal_is_wired_into_equity_scan_only_when_routable():
    """
    record_signal must be called from inside _run_equity_scan's per-ticker
    loop, gated on routable_signal — a HOLD or below-threshold signal has
    no trade_plan worth tracking, and calling it unconditionally would
    persist junk rows for every scanned ticker every cycle.
    """
    import inspect
    import app.main as m

    src = inspect.getsource(m._run_equity_scan)
    assert "from app.services.signal_outcome_tracker import record_signal" in src
    assert "if routable_signal:" in src
    # The import + call must both appear after the routable_signal gate,
    # not before it (a crude but effective ordering check).
    gate_idx = src.index("if routable_signal:")
    call_idx = src.index("await record_signal(signal)")
    assert call_idx > gate_idx


def test_compute_stats_ticker_breakdown_sorted_by_volume():
    outcomes = [
        {"ticker": "A", "status": "target_hit", "confidence": 0.7, "days_to_resolve": 1},
        {"ticker": "A", "status": "stop_hit", "confidence": 0.7, "days_to_resolve": 1},
        {"ticker": "A", "status": "target_hit", "confidence": 0.7, "days_to_resolve": 1},
        {"ticker": "B", "status": "target_hit", "confidence": 0.7, "days_to_resolve": 1},
    ]
    stats = compute_signal_outcome_stats(outcomes)
    assert stats["by_ticker"][0]["ticker"] == "A"
    assert stats["by_ticker"][0]["total"] == 3
    assert stats["by_ticker"][0]["hit_rate"] == pytest.approx(2 / 3, abs=0.001)


# ── the transaction bug that ate every equity signal ─────────────────────────

@pytest.mark.asyncio
async def test_record_signal_does_not_begin_a_second_transaction():
    """The bug this file's fake was rewritten to be able to see.

    record_signal ran `async with session.begin()` AFTER the dedup SELECT had
    already autobegun the transaction. A real AsyncSession raises
    InvalidRequestError on that second begin, so every routable BUY/SELL
    signal failed and signal_outcomes took no rows from #45 until this fix.

    Nothing surfaced it: the failure is swallowed into a logger.warning
    (correct — a tracking failure must not break the scan), and the old fake's
    begin() never raised. Asserted here explicitly, rather than left implicit
    in the other tests, so the reason is written down where the next person
    changing this function will read it.
    """
    session = _mock_session()
    signal = {
        "id": "sig-tx", "ticker": "AMD", "action": "BUY", "confidence": 0.6,
        "generated_at": "2026-01-05T14:30:00+00:00",
        "trade_plan": {"entry_price": 100.0, "stop_price": 96.0,
                       "target_price": 108.0, "target_move_pct": 8.0},
    }
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        result = await record_signal(signal)

    assert result is not None, (
        "record_signal returned None — the insert raised and was swallowed, "
        "which is exactly how this went unnoticed in production"
    )
    assert session.begin.call_count == 0, (
        "record_signal called session.begin() after the dedup SELECT had "
        "already autobegun the transaction; a real AsyncSession raises there"
    )


@pytest.mark.asyncio
async def test_record_signal_actually_commits_the_row():
    """add() without commit() writes nothing.

    Removing the `async with session.begin()` block also removed the implicit
    commit its context manager performed, so the commit has to be explicit. A
    test that only checks session.add was called would pass on a function that
    stages a row and then drops it.
    """
    session = _mock_session()
    signal = {
        "id": "sig-commit", "ticker": "INTC", "action": "SELL", "confidence": 0.5,
        "generated_at": "2026-01-05T14:30:00+00:00",
        "trade_plan": {"entry_price": 50.0, "stop_price": 52.0,
                       "target_price": 46.0, "target_move_pct": 8.0},
    }
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        await record_signal(signal)

    assert session.add.call_count == 1
    assert session.commit.await_count == 1, (
        "the row was staged but never committed — nothing reaches the database"
    )


@pytest.mark.asyncio
async def test_a_duplicate_is_still_skipped_without_writing():
    """The dedup path must not have been broken by the transaction change."""
    session = _mock_session(existing_id="already-there")
    signal = {
        "id": "sig-dup", "ticker": "AMD", "action": "BUY", "confidence": 0.6,
        "generated_at": "2026-01-05T14:30:00+00:00",
        "trade_plan": {"entry_price": 100.0, "stop_price": 96.0,
                       "target_price": 108.0, "target_move_pct": 8.0},
    }
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        result = await record_signal(signal)

    assert result == "already-there"
    assert session.add.call_count == 0
    assert session.commit.await_count == 0
