"""
A signal that resolved on day 2 eventually gets a full-window excursion.

check_pending_outcomes() selects `status == "pending"` and writes the resolution
the instant a barrier is touched. That is right for the operator — the outcome
is needed now, not in three weeks — but it means an early winner is measured
over the two bars that existed at that moment, lands with full_window_days == 2,
and is never selected again. _is_uncensored() rejects it forever.

Which would make the uncensoring decorative. MOST winners resolve early; if
none of them can ever be measured, _mfe_r keeps falling back to the censored
value and _censoring_ceiling_r sits exactly where it always did. The column
would fill up with numbers nothing is allowed to use. Raised in review on
PR #65, and it is the finding that decides whether any of this works.

enrich_incomplete_excursions() is the second pass. These tests pin what it must
and must not do — the "must not" being the more dangerous half: it re-walks
bars, so it could very easily rewrite a resolution that has already been
reported.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

pd = pytest.importorskip("pandas")

from app.services import signal_outcome_tracker as sot
from app.services.signal_outcome_tracker import (
    DEFAULT_MAX_HOLD_DAYS, enrich_incomplete_excursions,
)


def _bars(rows, start):
    idx = [start + timedelta(days=i + 1) for i in range(len(rows))]
    return pd.DataFrame(
        {"High": [r[0] for r in rows], "Low": [r[1] for r in rows],
         "Close": [r[2] for r in rows]},
        index=pd.DatetimeIndex(idx),
    )


def _row(*, rid="r1", generated_days_ago=5, window=2, status="target_hit"):
    gen = datetime.now(timezone.utc) - timedelta(days=generated_days_ago)
    return SimpleNamespace(
        id=rid, ticker="SPY", action="BUY", generated_at=gen, status=status,
        entry_price=Decimal("100"), stop_price=Decimal("98"),
        target_price=Decimal("104"), full_window_days=window,
    )


class _Session:
    """Minimal async-session stand-in: serves `rows`, captures writes."""
    def __init__(self, rows, sink):
        self._rows, self._sink = rows, sink

    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

    def begin(self):
        outer = self
        class _Tx:
            async def __aenter__(self): return outer
            async def __aexit__(self, *a): return False
        return _Tx()

    async def execute(self, stmt, params=None):
        if params is not None:
            self._sink.extend(params)
            return None
        rows = self._rows
        class _R:
            def scalars(self_inner):
                class _S:
                    def all(self_s): return rows
                return _S()
        return _R()


def _install(monkeypatch, rows, sink, bars):
    # AsyncSessionLocal is imported INSIDE the function, so it resolves from
    # app.core.database at call time — patching the tracker module would do
    # nothing. _fetch_daily_bars is module-level and patches normally.
    import app.core.database as db
    monkeypatch.setattr(db, "AsyncSessionLocal", lambda: _Session(rows, sink))
    async def _fetch(ticker, start): return bars
    monkeypatch.setattr(sot, "_fetch_daily_bars", _fetch)


@pytest.mark.asyncio
async def test_an_early_winner_becomes_measurable_once_the_bars_arrive(monkeypatch):
    """The whole point. Resolved on day 1 with a 2-bar window; now 25 bars
    exist, so it gets a complete measurement and stops being rejected."""
    row = _row(window=2, generated_days_ago=5)
    # Touches the target on day 1, then keeps running to +18%.
    bars = _bars([(104.0, 99.0, 103.0)] + [(118.0, 103.0, 117.0)] * 24,
                 row.generated_at)
    sink = []
    _install(monkeypatch, [row], sink, bars)

    out = await enrich_incomplete_excursions(max_hold_days=DEFAULT_MAX_HOLD_DAYS)

    assert out["enriched"] == 1
    assert out["completed"] == 1
    written = sink[0]
    assert written["full_window_days"] == DEFAULT_MAX_HOLD_DAYS
    assert written["mfe_full_pct"] == Decimal("0.1800")


@pytest.mark.asyncio
async def test_it_never_writes_a_resolution(monkeypatch):
    """The dangerous half. This pass re-walks bars, so it COULD rewrite a
    status or an exit price that has already been reported — and must not."""
    row = _row(window=2)
    bars = _bars([(104.0, 99.0, 103.0)] + [(118.0, 103.0, 117.0)] * 24,
                 row.generated_at)
    sink = []
    _install(monkeypatch, [row], sink, bars)

    await enrich_incomplete_excursions()

    forbidden = {"status", "exit_price", "resolved_at", "days_to_resolve",
                 "max_favorable_pct", "max_adverse_pct"}
    for payload in sink:
        assert not (forbidden & payload.keys()), (
            f"enrichment wrote resolution fields {forbidden & payload.keys()} — "
            f"it must only ever touch the excursion columns"
        )
        assert set(payload) == {"id", "mfe_full_pct", "mae_full_pct",
                                "full_window_days"}


@pytest.mark.asyncio
async def test_a_row_with_no_new_bars_is_left_alone(monkeypatch):
    """No new data means nothing to say. Writing the same value back would
    churn rows and make the summary claim progress that did not happen."""
    row = _row(window=3)
    bars = _bars([(104.0, 99.0, 103.0), (105.0, 102.0, 104.0),
                  (106.0, 103.0, 105.0)], row.generated_at)
    sink = []
    _install(monkeypatch, [row], sink, bars)

    out = await enrich_incomplete_excursions()

    assert out["enriched"] == 0
    assert sink == []


@pytest.mark.asyncio
async def test_an_old_row_that_was_already_measured_is_not_retried(monkeypatch):
    """Termination. Past the margin, whatever window the data supports is the
    final answer — a delisted ticker would otherwise be re-fetched forever."""
    row = _row(window=4, generated_days_ago=DEFAULT_MAX_HOLD_DAYS * 2 + 10)
    sink = []
    _install(monkeypatch, [row], sink, _bars([(104.0, 99.0, 103.0)] * 30,
                                             row.generated_at))

    out = await enrich_incomplete_excursions()

    assert out["candidates"] == 0, (
        "a row older than the margin with a measurement already recorded must "
        "drop out of the candidate set"
    )
    assert sink == []


@pytest.mark.asyncio
async def test_a_never_measured_row_is_picked_up_however_old(monkeypatch):
    """full_window_days IS NULL means pre-migration, or written before this
    pass existed. Migration 0031 backfilled nothing on purpose; this is where
    those rows get their excursions."""
    row = _row(window=None, generated_days_ago=400)
    bars = _bars([(104.0, 99.0, 103.0)] + [(118.0, 103.0, 117.0)] * 24,
                 row.generated_at)
    sink = []
    _install(monkeypatch, [row], sink, bars)

    out = await enrich_incomplete_excursions()

    assert out["candidates"] == 1
    assert out["enriched"] == 1


@pytest.mark.asyncio
async def test_a_bars_fetch_failure_does_not_take_the_pass_down(monkeypatch):
    row = _row(window=2)
    sink = []
    import app.core.database as db
    monkeypatch.setattr(db, "AsyncSessionLocal", lambda: _Session([row], sink))
    async def _boom(ticker, start): raise RuntimeError("yfinance down")
    monkeypatch.setattr(sot, "_fetch_daily_bars", _boom)

    out = await enrich_incomplete_excursions()

    assert out["enriched"] == 0
    assert out["tickers_covered"] == 0
