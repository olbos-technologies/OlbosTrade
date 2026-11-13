"""The 0DTE recorder against real PostgreSQL: what it writes, what it must not, and that it
is safe to run every minute and across restarts.

The unique key, the JSONB marks and the "today only" filter are all database behaviour, so a
mocked session would never evaluate them.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.zero_dte_observation import ZeroDteObservation as Obs
from app.services import zero_dte_recorder as rec

TEST_DB_URL = os.getenv("OLBOS_TEST_DATABASE_URL")
pg = pytest.mark.skipif(not TEST_DB_URL, reason="needs a real PostgreSQL; set OLBOS_TEST_DATABASE_URL")

# Wednesday 2026-11-11, 10:30 ET (EST).
T0 = datetime(2026, 11, 11, 15, 30, 0, tzinfo=timezone.utc)
TODAY = date(2026, 11, 11)


def opt(strike, bid, ask):
    return SimpleNamespace(strike=strike, bid=bid, ask=ask, last=(bid + ask) / 2)


def chain_at(now, *, calls=None, puts=None, spot=600.2, age_s=3):
    return SimpleNamespace(
        underlying="SPY", expiration=TODAY, underlying_price=spot,
        calls=calls if calls is not None else [
            opt(600, 1.90, 1.94), opt(601, 1.30, 1.34), opt(602, 0.84, 0.88)],
        puts=puts if puts is not None else [
            opt(598, 0.60, 0.64), opt(599, 0.95, 0.99), opt(600, 1.60, 1.64)],
        fetched_at=now - timedelta(seconds=age_s),
    )


class Fetcher:
    """A scripted stand-in for the broker's windowed chain fetch."""

    def __init__(self, chain_for=None, fail=False):
        self.calls = []
        self.fail = fail
        self.chain_for = chain_for

    async def __call__(self, symbol, expiry, **kw):
        self.calls.append((symbol, expiry, kw))
        if self.fail:
            raise TimeoutError("coordinator timed out")
        return self.chain_for(symbol, kw) if self.chain_for else chain_at(self.now)


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine(TEST_DB_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS zero_dte_observations CASCADE"))
        await conn.run_sync(Obs.__table__.create)
    yield async_sessionmaker(bind=engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS zero_dte_observations CASCADE"))
    await engine.dispose()


async def rows(factory):
    async with factory() as s:
        return (await s.execute(select(Obs).order_by(Obs.symbol, Obs.direction))).scalars().all()


async def tick(fetch, factory, now):
    fetch.now = now
    return await rec.record_tick(fetch, now=now, session_factory=factory)


# ── pure helpers ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("minute,expected", [
    (30, "10:30"), (35, "10:30"), (36, None), (59, None),
])
def test_due_slot_allows_a_short_grace(minute, expected):
    now = datetime(2026, 11, 11, 15, minute, 0, tzinfo=timezone.utc)      # 10:xx ET
    assert rec.due_slot(now) == expected


def test_due_slot_other_times():
    et = lambda h, m: datetime(2026, 11, 11, h + 5, m, tzinfo=timezone.utc)
    assert rec.due_slot(et(10, 0)) == "10:00"
    assert rec.due_slot(et(14, 0)) == "14:00"
    assert rec.due_slot(et(9, 59)) is None
    assert rec.due_slot(et(14, 10)) is None
    assert rec.due_slot(et(12, 30)) is None            # not a slot


def test_build_mark_computes_the_natural_and_mid_closing_credit():
    ch = chain_at(T0, calls=[opt(600, 1.50, 1.54), opt(602, 0.50, 0.54)])
    m = rec.build_mark(ch, option_type="call", long_strike=600.0, short_strike=602.0, minutes=5,
                       observed_at=T0, now=T0 + timedelta(seconds=305))
    assert m["quote_ok"] and m["minutes"] == 5 and m["elapsed_s"] == 305.0
    assert m["natural_credit"] == pytest.approx(1.50 - 0.54)       # sell long at bid, buy short at ask
    assert m["mid_credit"] == pytest.approx(1.52 - 0.52)


def test_build_mark_keeps_a_collapsed_leg_as_a_real_outcome():
    ch = chain_at(T0, calls=[opt(600, 0.0, 0.03), opt(602, 0.0, 0.02)])
    m = rec.build_mark(ch, option_type="call", long_strike=600.0, short_strike=602.0, minutes=10,
                       observed_at=T0, now=T0 + timedelta(seconds=610))
    assert m["quote_ok"] is True and m["natural_credit"] == pytest.approx(-0.02)


@pytest.mark.parametrize("calls,problem", [
    ([opt(600, 1.0, 1.1)], "leg missing"),                         # short strike absent
    ([opt(600, 1.0, 0.0), opt(602, 0.5, 0.6)], "not usable"),       # long has no ask
    ([opt(600, 1.2, 1.1), opt(602, 0.5, 0.6)], "not usable"),       # crossed
])
def test_build_mark_flags_unreadable_quotes_and_never_fills_them_in(calls, problem):
    m = rec.build_mark(chain_at(T0, calls=calls), option_type="call", long_strike=600.0,
                       short_strike=602.0, minutes=5, observed_at=T0, now=T0 + timedelta(seconds=300))
    assert m["quote_ok"] is False and problem in m["problem"]
    assert m["natural_credit"] is None and m["mid_credit"] is None


def test_the_recorder_imports_nothing_that_can_reach_an_order_path():
    import inspect
    imports = [ln for ln in inspect.getsource(rec).splitlines() if ln.startswith(("import ", "from "))]
    assert imports, "read no import lines: the guard would pass vacuously"
    for ln in imports:
        assert not any(w in ln for w in ("trade_desk", "execution", "coordinator", "broker", "order")), ln


# ── observations ─────────────────────────────────────────────────────────────

@pg
@pytest.mark.asyncio
async def test_a_due_slot_records_both_directions_for_both_symbols(factory):
    f = Fetcher()
    report = await tick(f, factory, T0)
    assert report["status"] == "ok" and report["slot"] == "10:30" and report["observed"] == 4
    got = await rows(factory)
    assert [(r.symbol, r.direction) for r in got] == [
        ("QQQ", "bearish"), ("QQQ", "bullish"), ("SPY", "bearish"), ("SPY", "bullish")]
    bull = next(r for r in got if r.symbol == "SPY" and r.direction == "bullish")
    assert bull.proposed and bull.option_type == "call" and (bull.long_strike, bull.short_strike) == (600.0, 602.0)
    assert (bull.long_bid, bull.long_ask, bull.short_bid, bull.short_ask) == (1.90, 1.94, 0.84, 0.88)
    assert bull.mid_debit == 1.06 and bull.natural_debit == pytest.approx(1.10)
    assert bull.trade_date == TODAY and bull.slot == "10:30" and bull.marks == [] and bull.spot == 600.2
    # one windowed request per symbol, both sides: never the whole chain
    assert [(c[0], c[2]) for c in f.calls] == [
        ("SPY", {"window": rec.OBSERVE_WINDOW, "option_types": ("call", "put")}),
        ("QQQ", {"window": rec.OBSERVE_WINDOW, "option_types": ("call", "put")})]
    assert all(c[1] == "2026-11-11" for c in f.calls)


@pg
@pytest.mark.asyncio
async def test_running_the_same_minute_again_changes_nothing(factory):
    f = Fetcher()
    await tick(f, factory, T0)
    n = len(f.calls)
    again = await tick(f, factory, T0 + timedelta(seconds=40))
    assert again["observed"] == 0 and len(f.calls) == n and len(await rows(factory)) == 4


@pg
@pytest.mark.asyncio
async def test_a_refusal_is_recorded_with_its_reasons(factory):
    wide = chain_at(T0, calls=[opt(600, 1.00, 2.00), opt(602, 0.84, 0.88)])
    await tick(Fetcher(chain_for=lambda s, kw: wide), factory, T0)
    bull = next(r for r in await rows(factory) if r.symbol == "SPY" and r.direction == "bullish")
    assert bull.proposed is False and any("too wide" in x for x in bull.refusal_reasons)
    assert bull.long_strike is None and bull.mid_debit is None and bull.spot == 600.2
    assert next(r for r in await rows(factory) if r.symbol == "SPY" and r.direction == "bearish").proposed


@pg
@pytest.mark.asyncio
async def test_a_failed_fetch_is_recorded_as_unavailable_and_does_not_crash(factory):
    report = await tick(Fetcher(fail=True), factory, T0)
    assert report["observed"] == 4
    got = await rows(factory)
    assert all(not r.proposed and r.spot is None and "chain unavailable" in r.refusal_reasons[0] for r in got)
    assert "TimeoutError" in got[0].refusal_reasons[0]


@pg
@pytest.mark.asyncio
async def test_nothing_is_recorded_outside_market_hours(factory):
    f = Fetcher()
    saturday = datetime(2026, 11, 14, 15, 30, tzinfo=timezone.utc)
    after_close = datetime(2026, 11, 11, 21, 30, tzinfo=timezone.utc)          # 16:30 ET
    for t in (saturday, after_close):
        assert (await tick(f, factory, t))["status"] == "market_closed"
    assert f.calls == [] and await rows(factory) == []


@pg
@pytest.mark.asyncio
async def test_a_time_with_no_slot_records_nothing(factory):
    f = Fetcher()
    t = datetime(2026, 11, 11, 17, 30, tzinfo=timezone.utc)                    # 12:30 ET: open, not a slot
    assert (await tick(f, factory, t)) == {"status": "ok", "slot": None, "observed": 0, "marks": 0}
    assert f.calls == []


# ── marks ────────────────────────────────────────────────────────────────────

def mark_chain(now):
    return chain_at(now, calls=[opt(600, 1.50, 1.54), opt(601, 1.00, 1.04), opt(602, 0.50, 0.54)],
                    puts=[opt(598, 0.30, 0.34), opt(599, 0.60, 0.64), opt(600, 1.00, 1.04)], spot=600.9)


@pg
@pytest.mark.asyncio
async def test_marks_are_taken_at_5_and_10_minutes_with_real_closing_prices(factory):
    f = Fetcher()
    await tick(f, factory, T0)
    f.chain_for = lambda s, kw: mark_chain(f.now)
    f.calls.clear()
    r5 = await tick(f, factory, T0 + timedelta(seconds=310))
    r10 = await tick(f, factory, T0 + timedelta(seconds=615))
    assert r5["marks"] == 4 and r10["marks"] == 4
    spy_bull = next(r for r in await rows(factory) if r.symbol == "SPY" and r.direction == "bullish")
    assert [m["minutes"] for m in spy_bull.marks] == [5, 10]
    m5 = spy_bull.marks[0]
    assert m5["quote_ok"] and m5["spot"] == 600.9
    assert m5["natural_credit"] == pytest.approx(1.50 - 0.54)
    entry_natural = spy_bull.natural_debit
    assert (m5["natural_credit"] - entry_natural) == pytest.approx(0.96 - 1.10)    # the real round-trip result


@pg
@pytest.mark.asyncio
async def test_a_mark_fetch_is_small_and_windowed_around_the_legs(factory):
    f = Fetcher()
    await tick(f, factory, T0)
    f.chain_for = lambda s, kw: mark_chain(f.now)
    f.calls.clear()
    await tick(f, factory, T0 + timedelta(seconds=310))
    assert len(f.calls) == 4                                    # one per (symbol, side): no more
    spy_call = next(c for c in f.calls if c[0] == "SPY" and c[2]["option_types"] == ("call",))
    assert spy_call[2]["center"] == 600.0 and spy_call[2]["window"] == 2 + rec.MARK_EXTRA_STRIKES


@pg
@pytest.mark.asyncio
async def test_a_mark_is_never_duplicated(factory):
    f = Fetcher()
    await tick(f, factory, T0)
    f.chain_for = lambda s, kw: mark_chain(f.now)
    await tick(f, factory, T0 + timedelta(seconds=310))
    n = len(f.calls)
    again = await tick(f, factory, T0 + timedelta(seconds=340))
    assert again["marks"] == 0 and len(f.calls) == n
    assert all(len(r.marks) == 1 for r in await rows(factory) if r.proposed)


@pg
@pytest.mark.asyncio
async def test_a_mark_that_is_too_late_is_left_missing_not_back_filled(factory):
    f = Fetcher()
    await tick(f, factory, T0)
    f.chain_for = lambda s, kw: mark_chain(f.now)
    f.calls.clear()
    late = await tick(f, factory, T0 + timedelta(seconds=300 + rec.MARK_GRACE_S + 5))   # 5-min mark missed
    assert late["marks"] == 0 and f.calls == []
    assert all(r.marks == [] for r in await rows(factory))


@pg
@pytest.mark.asyncio
async def test_a_failed_mark_fetch_is_retried_on_the_next_tick(factory):
    f = Fetcher()
    await tick(f, factory, T0)
    f.fail = True
    assert (await tick(f, factory, T0 + timedelta(seconds=305)))["marks"] == 0
    assert all(r.marks == [] for r in await rows(factory))      # nothing invented for the failure
    f.fail = False
    f.chain_for = lambda s, kw: mark_chain(f.now)
    assert (await tick(f, factory, T0 + timedelta(seconds=365)))["marks"] == 4


@pg
@pytest.mark.asyncio
async def test_an_unreadable_mark_is_recorded_as_unreadable(factory):
    f = Fetcher()
    await tick(f, factory, T0)
    f.chain_for = lambda s, kw: chain_at(f.now, calls=[opt(600, 1.5, 1.54)], puts=[opt(600, 1.0, 1.04)])
    await tick(f, factory, T0 + timedelta(seconds=305))
    spy_bull = next(r for r in await rows(factory) if r.symbol == "SPY" and r.direction == "bullish")
    assert spy_bull.marks[0]["quote_ok"] is False and spy_bull.marks[0]["natural_credit"] is None


@pg
@pytest.mark.asyncio
async def test_only_todays_observations_are_marked(factory):
    f = Fetcher()
    await tick(f, factory, T0)
    async with factory() as s:                                  # pretend these were taken yesterday
        for r in (await s.execute(select(Obs))).scalars().all():
            r.trade_date = date(2026, 11, 10)
        await s.commit()
    f.chain_for = lambda s, kw: mark_chain(f.now)
    f.calls.clear()
    f.now = T0 + timedelta(seconds=305)
    assert await rec._mark_due(f, factory, f.now) == 0 and f.calls == []      # the marking step alone


@pg
@pytest.mark.asyncio
async def test_refused_observations_get_no_marks(factory):
    f = Fetcher(fail=True)
    await tick(f, factory, T0)
    f.fail = False
    f.chain_for = lambda s, kw: mark_chain(f.now)
    f.calls.clear()
    assert (await tick(f, factory, T0 + timedelta(seconds=305)))["marks"] == 0 and f.calls == []


@pg
@pytest.mark.asyncio
async def test_the_unique_key_rejects_a_duplicate_row(factory):
    from sqlalchemy.exc import IntegrityError
    f = Fetcher()
    await tick(f, factory, T0)
    async with factory() as s:
        s.add(Obs(trade_date=TODAY, slot="10:30", symbol="SPY", direction="bullish",
                  observed_at=T0, proposed=False, marks=[]))
        with pytest.raises(IntegrityError):
            await s.commit()
