"""The options funnel's queries, against real PostgreSQL.

The funnel groups a JSONB field (`payload->>'reason'`) and filters by window, kind and asset
type. Against a mocked session those clauses are never evaluated, so a wrong filter would pass
every test. Here the three source tables hold real rows: some that must be counted and, next to
them, rows that must NOT be (equity events, other event kinds, rows older than the window).
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.execution_event import ExecutionEvent
from app.models.options_scan_rejection import OptionsScanRejection
from app.models.options_signal_history import OptionsSignalHistory
from app.services.options_funnel import build_funnel

TEST_DB_URL = os.getenv("OLBOS_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not TEST_DB_URL, reason="needs a real PostgreSQL; set OLBOS_TEST_DATABASE_URL"),
]

NOW = datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc)
TABLES = (ExecutionEvent, OptionsScanRejection, OptionsSignalHistory)


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        for m in TABLES:
            await conn.execute(text(f"DROP TABLE IF EXISTS {m.__tablename__} CASCADE"))
            await conn.run_sync(m.__table__.create)
    yield async_sessionmaker(bind=engine, expire_on_commit=False)
    async with engine.begin() as conn:
        for m in TABLES:
            await conn.execute(text(f"DROP TABLE IF EXISTS {m.__tablename__} CASCADE"))
    await engine.dispose()


def _rej(reason, *, occurrences=1, days_ago=1.0, ticker="AAPL"):
    t = NOW - timedelta(days=days_ago)
    return OptionsScanRejection(ticker=ticker, strategy=None, reason=reason, regime="high_vol_trending",
                                occurrences=occurrences, first_seen_at=t, last_seen_at=t)


def _hist(days_ago=1.0, ticker="AAPL"):
    return OptionsSignalHistory(
        signal_id=str(uuid.uuid4()), ticker=ticker, strategy="bear_call_spread", action="SELL_SPREAD",
        confidence=Decimal("0.6500"), pop=Decimal("0.7200"), signal_score=Decimal("0.6500"), quantity=1,
        iv_rank=Decimal("55.00"), regime="high_vol_trending", option_type="call",
        short_strike=Decimal("200"), long_strike=Decimal("210"), expiration=date(2026, 12, 18), dte=40,
        net_credit=Decimal("1.50"), max_loss=Decimal("8.50"), breakeven=Decimal("201.5"), sigma=Decimal("0.2"),
        vix_used=Decimal("28.00"), credit_source="live", generated_at=NOW - timedelta(days=days_ago))


def _ev(kind="execution", *, asset="options", status="blocked", reason=None, days_ago=0.5):
    payload = {"reason": reason} if reason is not None else {}
    return ExecutionEvent(kind=kind, ticker="AAPL", asset_type=asset, status=status, payload=payload,
                          created_at=NOW - timedelta(days=days_ago))


async def _funnel(factory, *rows, days=7, mode="autopilot"):
    async with factory() as s, s.begin():
        s.add_all(rows)
    async with factory() as s:
        return await build_funnel(s, days=days, now=NOW, mode=mode)


def _stage(r, key):
    return next(x for x in r["stages"] if x["key"] == key)


async def test_an_empty_database_says_no_data_not_zero_rejections(factory):
    r = await _funnel(factory)
    assert r["verdict"]["code"] == "no_data" and r["scanned"] == 0
    assert all(s["count"] == 0 for s in r["stages"])


async def test_scan_rejections_count_occurrences_and_bucket_their_reasons(factory):
    r = await _funnel(
        factory,
        _rej("bull_put_spread: RSI too weak: 41.0 (need >45)", occurrences=5),
        _rej("bull_put_spread: RSI too weak: 38.2 (need >45)", occurrences=3, ticker="MSFT"),
        _rej("insufficient price history", occurrences=2, ticker="ZZZ"),
    )
    st = _stage(r, "scan_rejected")
    assert st["count"] == 10
    assert st["reasons"][0] == {"reason": "entry_rule: rsi", "count": 8}      # the two RSI rows are one reason
    assert {"reason": "insufficient_price_history", "count": 2} in st["reasons"]


async def test_a_combined_entry_row_counts_toward_each_strategy_reason_with_its_occurrences(factory):
    r = await _funnel(factory, _rej(
        "bull_put_spread: Price below 20-day SMA — no bullish bias; bear_call_spread: IV rank too low: 22.0 (need >30)",
        occurrences=4))
    reasons = {x["reason"]: x["count"] for x in _stage(r, "scan_rejected")["reasons"]}
    assert reasons == {"entry_rule: price_vs_20day_average": 4, "entry_rule: iv_rank_too_low": 4}
    assert _stage(r, "scan_rejected")["count"] == 4                          # four evaluations, not eight


async def test_rows_outside_the_window_are_not_counted(factory):
    r = await _funnel(factory, _rej("insufficient price history", days_ago=2), _rej("insufficient price history", days_ago=9),
                      _hist(days_ago=2), _hist(days_ago=9), _ev(days_ago=2), _ev(days_ago=9))
    assert _stage(r, "scan_rejected")["count"] == 1
    assert _stage(r, "qualified")["count"] == 1
    assert _stage(r, "execution_blocked")["count"] == 1


async def test_the_window_follows_the_days_argument(factory):
    rows = [_ev(days_ago=0.5), _ev(days_ago=2.5), _ev(days_ago=5)]
    assert _stage(await _funnel(factory, *rows, days=1), "execution_blocked")["count"] == 1
    async with factory() as s:
        wide = await build_funnel(s, days=7, now=NOW, mode="autopilot")
    assert _stage(wide, "execution_blocked")["count"] == 3


async def test_only_options_execution_events_are_counted(factory):
    r = await _funnel(
        factory,
        _ev(status="blocked", reason="frequency_controller: below_min_confidence: 0.6 < 0.7 (aggressive)"),
        _ev(asset="equity", status="blocked", reason="frequency_controller: daily_cap: 10/10 for aggressive"),
        _ev(kind="mode_change", status="blocked", reason="kill_switch"),            # other event kinds that happen
        _ev(kind="pending_approval", status="blocked", reason="kill_switch"),       # to carry a status must not leak in
        _ev(kind="pending_approval", status="pending"),                              # the queue is its own stage
        _ev(kind="pending_approval", asset="equity", status="pending"),              # an equity approval is not an options one
    )
    # two OPTIONS queue rows (the odd "blocked"-status one and the pending one); the equity row is excluded
    assert _stage(r, "copilot_queue")["count"] == 2
    blocked = _stage(r, "execution_blocked")
    assert blocked["count"] == 1
    assert blocked["reasons"] == [{"reason": "frequency_controller: below_min_confidence", "count": 1}]


async def test_execution_reasons_that_differ_only_in_numbers_are_one_bucket(factory):
    r = await _funnel(factory, *[_ev(reason=f"frequency_controller: below_min_confidence: 0.{n} < 0.70 (aggressive)")
                                 for n in (51, 55, 58, 62, 66)])
    assert _stage(r, "execution_blocked")["reasons"] == [
        {"reason": "frequency_controller: below_min_confidence", "count": 5}]


async def test_a_missing_reason_is_unspecified_not_dropped(factory):
    r = await _funnel(factory, _ev(status="blocked", reason=None))
    assert _stage(r, "execution_blocked")["reasons"] == [{"reason": "unspecified", "count": 1}]


async def test_the_users_likely_situation_candidates_qualify_and_the_frequency_controller_blocks_them(factory):
    r = await _funnel(
        factory,
        *[_rej("bull_put_spread: Price below 20-day SMA — no bullish bias", occurrences=13, ticker=t) for t in ("A", "B", "C")],
        *[_hist(ticker=t) for t in ("AAPL", "MSFT", "NVDA")],
        *[_ev(reason="frequency_controller: below_min_confidence: 0.6 < 0.70 (aggressive)") for _ in range(6)],
        _ev(reason="frequency_controller: pop_below_floor: 0.66 < 0.70 (aggressive)"),
    )
    assert r["verdict"]["code"] == "blocked_at_execution"
    assert "frequency_controller: below_min_confidence (6)" in r["verdict"]["text"]
    assert _stage(r, "qualified")["count"] == 3 and _stage(r, "submitted")["count"] == 0


async def test_a_submitted_order_makes_the_verdict_trading(factory):
    r = await _funnel(factory, _hist(), _ev(status="submitted"), _ev(reason="liquidity_gate: spread_too_wide (12.5%)"))
    assert r["verdict"]["code"] == "trading" and _stage(r, "submitted")["count"] == 1


async def test_copilot_candidates_queued_but_never_approved_are_reported_as_waiting(factory):
    r = await _funnel(factory, _hist(), *[_ev(kind="pending_approval", status="pending") for _ in range(5)], mode="copilot")
    assert r["verdict"]["code"] == "waiting_for_approval"
    q = _stage(r, "copilot_queue")
    assert q["count"] == 5 and q["reasons"] == [{"reason": "pending", "count": 5}]


async def test_a_copilot_approval_stops_the_waiting_verdict(factory):
    """One approval among pending ones: the candidates are being acted on, so the answer is about
    execution, not about waiting for a person."""
    r = await _funnel(factory, _hist(),
                      _ev(kind="pending_approval", status="approved"),
                      *[_ev(kind="pending_approval", status="pending") for _ in range(4)],
                      _ev(status="blocked", reason="liquidity_gate: spread_too_wide (12.5%)"), mode="copilot")
    assert r["verdict"]["code"] == "blocked_at_execution"
    assert _stage(r, "copilot_queue")["count"] == 5


async def test_manual_mode_with_qualified_candidates_says_nothing_routes(factory):
    r = await _funnel(factory, _hist(), mode="manual")
    assert r["verdict"]["code"] == "mode_manual"


async def test_autopilot_candidates_with_no_execution_attempt_are_flagged(factory):
    r = await _funnel(factory, _hist(), mode="autopilot")
    assert r["verdict"]["code"] == "qualified_but_never_dispatched"


async def test_nothing_qualifies_names_the_scan_reason(factory):
    r = await _funnel(factory, _rej("bull_put_spread: RSI too weak: 40 (need >45)", occurrences=30))
    assert r["verdict"]["code"] == "nothing_qualifies" and "entry_rule: rsi (30)" in r["verdict"]["text"]


async def test_the_report_carries_the_context_it_was_given(factory):
    async with factory() as s:
        r = await build_funnel(s, days=3, now=NOW, mode="autopilot",
                               regime={"name": "high_vol_trending", "options_allowed": True},
                               frequency_rule={"min_confidence": 0.7})
    assert r["window_days"] == 3 and r["execution_mode"] == "autopilot"
    assert r["regime"]["name"] == "high_vol_trending" and r["frequency_rule"]["min_confidence"] == 0.7
    assert r["notes"], "the caveats must travel with the numbers"


# ── the route ────────────────────────────────────────────────────────────────

def _client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app, raise_server_exceptions=False)


async def test_the_route_validates_the_window(factory):
    c = _client()
    assert c.get("/api/options/funnel?days=0").status_code == 422
    assert c.get("/api/options/funnel?days=91").status_code == 422


async def test_the_route_returns_the_funnel(factory):
    async with factory() as s, s.begin():
        s.add_all([_hist(), _ev(reason="liquidity_gate: spread_too_wide (12.5%)")])
    with patch("app.core.database.AsyncSessionLocal", factory):
        resp = _client().get("/api/options/funnel?days=7")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["window_days"] == 7 and {s["key"] for s in body["stages"]} >= {"scan_rejected", "qualified", "submitted"}
    assert body["execution_mode"] in {"manual", "copilot", "autopilot", "unknown"}


async def test_a_failure_inside_the_funnel_query_is_an_error_not_a_page_of_zeros():
    """The session opens fine and the QUERY fails (a SQL error, a dropped connection mid-read)."""
    class OpensButFails:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a, **k):
            raise RuntimeError("connection reset mid-query")

    with patch("app.core.database.AsyncSessionLocal", lambda: OpensButFails()):
        resp = _client().get("/api/options/funnel")
    assert resp.status_code == 500


async def test_a_database_failure_is_an_error_not_a_page_of_zeros():
    class Broken:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *a):
            return False

    with patch("app.core.database.AsyncSessionLocal", lambda: Broken()):
        resp = _client().get("/api/options/funnel")
    assert resp.status_code == 500


async def test_the_context_helper_reports_the_active_trading_modes_floors():
    from app.api.routes.options import _funnel_context
    mode, regime, rule = _funnel_context()
    assert mode in {"manual", "copilot", "autopilot"}
    assert rule and {"min_confidence", "min_pop", "max_risk_score", "credit_spread_min_pop"} <= set(rule)
