"""
Tests for the read-only crypto phase.

Two things here are load-bearing beyond ordinary coverage:

* ``test_crypto_scan_never_routes`` asserts the guarantee the whole phase rests
  on. It patches the execution entry point and proves the scan does not reach
  it, so "crypto cannot trade yet" is verified rather than asserted in a
  docstring.
* ``test_record_signal_dedup_filters_on_the_signals_asset_type`` inspects the
  compiled SQL of the dedup lookup, not just the inserted row. A filter pinned
  to "equity" while the insert wrote "crypto" would find nothing, insert again
  on every scan tick, and rebuild the ~45x duplication signal_outcome_tracker's
  docstring exists to describe — and every other test here would still pass.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError, InvalidRequestError

from app.broker.broker_interface import Bar
from app.services.crypto_signal_engine import (
    CRYPTO_SCORING_VERSION,
    MIN_BARS,
    is_crypto_symbol,
    normalize_crypto_symbol,
    prices_representable,
    to_alpaca_symbol,
)
from app.services.crypto_scan import CRYPTO_REGIME_UNCLASSIFIED, run_crypto_scan
from app.services.signal_outcome_tracker import record_signal


# ── symbol handling ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("BTC/USD",  "BTC-USD"),
    ("BTC-USD",  "BTC-USD"),
    ("btcusd",   "BTC-USD"),
    (" eth-usd ", "ETH-USD"),
    ("ETHUSDT",  "ETH-USDT"),
    ("SOLUSDC",  "SOL-USDC"),
    ("AAPL",     "AAPL"),
])
def test_normalize_crypto_symbol(raw, expected):
    assert normalize_crypto_symbol(raw) == expected


def test_normalize_prefers_longest_quote_suffix():
    """USDT must not be read as USD with a stray T left on the base."""
    assert normalize_crypto_symbol("BTCUSDT") == "BTC-USDT"


def test_to_alpaca_symbol_only_replaces_the_pair_separator():
    assert to_alpaca_symbol("BTC-USD") == "BTC/USD"
    assert to_alpaca_symbol("btcusd") == "BTC/USD"


def test_is_crypto_symbol():
    assert is_crypto_symbol("BTC-USD")
    assert is_crypto_symbol("ETH/USDT")
    assert not is_crypto_symbol("AAPL")
    assert not is_crypto_symbol("BRK-B")   # dash, but not a crypto quote


# ── price precision guard ────────────────────────────────────────────────────

def test_prices_representable_accepts_a_normal_btc_plan():
    assert prices_representable(110_000.0, 104_500.0, 121_000.0)


def test_prices_representable_rejects_sub_cent_collapse():
    """A sub-cent coin's entry/stop/target all quantise to 0.0000."""
    assert not prices_representable(0.00002, 0.000019, 0.000021)


def test_prices_representable_rejects_a_triple_that_collapses_to_one_value():
    """Distinct in float, identical once stored — a zero-risk trade that never existed."""
    assert not prices_representable(1.0, 1.00001, 1.00002)


def test_prices_representable_rejects_none_and_out_of_range():
    assert not prices_representable(None)
    assert not prices_representable(100.0, None, 120.0)
    assert not prices_representable(1e12)
    assert not prices_representable(-5.0)


def test_default_crypto_watchlist_fits_the_db_columns():
    """
    signal_outcomes.ticker is String(10). A longer symbol would raise on insert
    inside record_signal's blanket try/except, so every crypto row would be
    silently dropped with only a logger.warning to show for it.
    """
    from app.core.config import settings

    watchlist = settings.get_crypto_watchlist()
    assert watchlist, "default crypto watchlist must not be empty"
    too_long = [s for s in watchlist if len(s) > 10]
    assert not too_long, f"symbols exceed ticker String(10): {too_long}"
    assert all(is_crypto_symbol(s) for s in watchlist)


def test_get_crypto_watchlist_normalises_and_dedups(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "crypto_watchlist", "BTC/USD, btcusd ,ETH-USD,, eth/usd")
    assert settings.get_crypto_watchlist() == ["BTC-USD", "ETH-USD"]


def test_watchlist_drops_non_crypto_symbols(monkeypatch):
    """
    Raised in review on #84. An override of CRYPTO_WATCHLIST=AAPL would fetch
    equity bars, write them with asset_type="crypto", and contaminate the one
    cohort this phase exists to keep clean — while looking like it worked.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "crypto_watchlist", "BTC-USD,AAPL,ETH/USD,SPY,BRK-B")
    assert settings.get_crypto_watchlist() == ["BTC-USD", "ETH-USD"]


def test_watchlist_of_only_equities_is_empty_not_passthrough(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "crypto_watchlist", "AAPL,MSFT")
    assert settings.get_crypto_watchlist() == []


# ── scan pipeline ────────────────────────────────────────────────────────────

def _bars(n: int = 260, start: float = 100.0, step: float = 0.5) -> list[Bar]:
    """An uptrending daily series long enough for EMA200 to compute."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    out: list[Bar] = []
    for i in range(n):
        close = start + i * step
        out.append(Bar(
            timestamp=base + timedelta(days=i),
            open=Decimal(str(round(close - 0.2, 4))),
            high=Decimal(str(round(close + 0.6, 4))),
            low=Decimal(str(round(close - 0.6, 4))),
            close=Decimal(str(round(close, 4))),
            volume=1_000_000 + i,
        ))
    return out


def _fetcher(bars: list[Bar]):
    async def _fetch(symbol: str, limit: int = 250) -> list[Bar]:
        return bars[-limit:]
    return _fetch


@pytest.fixture
def one_symbol(monkeypatch):
    """Scan exactly one symbol, so counters are unambiguous."""
    from app.core.config import settings

    monkeypatch.setattr(settings, "crypto_enabled", True)
    monkeypatch.setattr(settings, "crypto_watchlist", "BTC-USD")
    monkeypatch.setattr(settings, "crypto_min_confidence", 0.5)
    return settings


@pytest.fixture
def fake_indicators(monkeypatch):
    """
    Stand in for compute_indicators.

    Patched rather than computed for real because these tests exercise the crypto
    *plumbing* — symbol handling, guards, recording, and the absence of a routing
    path — not the indicator math, which has its own tests next door. The real
    function needs the `ta` native package, so depending on it here would couple
    the phase-1 guarantee test to an optional build dependency.
    """
    indicators = {
        "close": 110_000.0, "atr": 2_500.0, "rsi": 58.0, "macd": 120.0,
        "bb_pct_b": 0.7, "volume_ratio": 1.4, "above_ema200": True, "adx": 28.0,
    }
    monkeypatch.setattr(
        "app.services.equity_signal_engine.compute_indicators",
        MagicMock(return_value=dict(indicators)),
    )
    return indicators


@pytest.fixture
def no_account_call(monkeypatch):
    """Keep the advisory sizing fetch off the broker in tests."""
    monkeypatch.setattr(
        "app.services.crypto_scan._account_value", AsyncMock(return_value=100_000.0)
    )


@pytest.mark.asyncio
async def test_scan_returns_early_when_disabled(monkeypatch, one_symbol):
    monkeypatch.setattr(one_symbol, "crypto_enabled", False)
    fetch = AsyncMock()
    summary = await run_crypto_scan(bars_fetcher=fetch)
    assert summary["enabled"] is False
    assert summary["scanned"] == 0
    fetch.assert_not_called()


@pytest.mark.asyncio
async def test_scan_records_a_routable_signal_as_crypto(one_symbol, no_account_call, fake_indicators):
    recorder = AsyncMock(return_value="row-1")
    with patch("app.services.equity_signal_engine.score_equity_signal",
               return_value=("BUY", 0.88, {"trend": 3.0})), \
         patch("app.services.signal_outcome_tracker.record_signal", recorder):
        summary = await run_crypto_scan(bars_fetcher=_fetcher(_bars()))

    assert summary["scanned"] == 1
    assert summary["routable"] == 1
    assert summary["recorded"] == 1

    recorded = recorder.await_args.args[0]
    assert recorded["ticker"] == "BTC-USD"
    assert recorded["asset_type"] == "crypto"
    assert recorded["action"] == "BUY"
    assert recorded["signal_engine_version"] == CRYPTO_SCORING_VERSION
    assert recorded["regime"] == CRYPTO_REGIME_UNCLASSIFIED
    # No crypto orderflow feed exists — the value must be the stated neutral,
    # never a borrowed equity score.
    assert recorded["orderflow_score"] == 0.0
    assert recorded["trade_plan"]["entry_price"] > 0


@pytest.mark.asyncio
async def test_scan_scores_without_an_orderflow_input(one_symbol, no_account_call, fake_indicators):
    """
    The scorer must be called with orderflow left at its own neutral default,
    not with a fabricated number and not with None (which would raise on the
    `orderflow_score > threshold` comparison inside it).
    """
    scorer = MagicMock(return_value=("HOLD", 0.2, {}))
    with patch("app.services.equity_signal_engine.score_equity_signal", scorer):
        await run_crypto_scan(bars_fetcher=_fetcher(_bars()))

    assert scorer.call_count == 1
    assert "orderflow_score" not in scorer.call_args.kwargs
    assert len(scorer.call_args.args) == 1   # indicators only


@pytest.mark.asyncio
async def test_scan_never_routes(one_symbol, no_account_call, fake_indicators):
    """
    THE phase-1 guarantee: a high-confidence, routable crypto signal reaches the
    recorder and nothing else. handle_signal is the equity scan's execution entry
    point; if crypto ever grows a path to it, this fails.
    """
    router = AsyncMock()
    with patch("app.services.equity_signal_engine.score_equity_signal",
               return_value=("BUY", 0.99, {})), \
         patch("app.services.signal_outcome_tracker.record_signal",
               AsyncMock(return_value="row-1")), \
         patch("app.api.routes.trade_desk.handle_signal", router), \
         patch("app.services.trade_frequency_controller."
               "trade_frequency_controller.evaluate") as gate:
        summary = await run_crypto_scan(bars_fetcher=_fetcher(_bars()))

    assert summary["recorded"] == 1
    router.assert_not_awaited()
    gate.assert_not_called()


def test_crypto_modules_import_nothing_that_can_place_an_order():
    """
    Static counterpart to test_scan_never_routes.

    The runtime test proves one high-confidence signal did not reach
    handle_signal. This proves no crypto module IMPORTS an execution entry point
    at all, on any branch — including one a test never happens to take.

    AST-based, not a substring scan of the source: these modules discuss
    handle_signal at length in their docstrings, and a text search would either
    fail on the prose or be loosened until it stopped catching real imports.
    """
    import ast
    import inspect

    import app.api.routes.crypto as crypto_routes
    import app.services.crypto_scan as crypto_scan
    import app.services.crypto_signal_engine as crypto_engine

    forbidden = {
        "handle_signal", "place_equity_order", "place_order", "submit_order",
        "place_options_order", "close_position", "get_broker",
        "trade_frequency_controller",
    }

    for module in (crypto_scan, crypto_engine, crypto_routes):
        tree = ast.parse(inspect.getsource(module))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.update(a.asname or a.name for a in node.names)
            elif isinstance(node, ast.Import):
                imported.update((a.asname or a.name).split(".")[0] for a in node.names)
        offenders = imported & forbidden
        assert not offenders, f"{module.__name__} imports execution symbols: {offenders}"


@pytest.mark.asyncio
async def test_scan_skips_symbols_without_enough_bars(one_symbol, no_account_call):
    recorder = AsyncMock()
    with patch("app.services.signal_outcome_tracker.record_signal", recorder):
        summary = await run_crypto_scan(bars_fetcher=_fetcher(_bars(n=MIN_BARS - 1)))

    assert summary["scanned"] == 1
    assert summary["skipped_insufficient_bars"] == 1
    assert summary["signals"] == 0
    recorder.assert_not_awaited()


@pytest.mark.asyncio
async def test_price_precision_guard_blocks_recording_but_keeps_the_signal(
    one_symbol, no_account_call, fake_indicators
):
    """
    An unrepresentable plan must not reach the recorder, and must not vanish
    either — it stays in the display feed marked non-routable with the reason
    attached, so the skip is visible instead of silent.
    """
    recorder = AsyncMock()
    with patch("app.services.equity_signal_engine.score_equity_signal",
               return_value=("BUY", 0.9, {})), \
         patch("app.services.equity_signal_engine.compute_equity_trade_plan",
               return_value={"entry_price": 0.00002, "stop_price": 0.000019,
                             "target_price": 0.000021}), \
         patch("app.services.signal_outcome_tracker.record_signal", recorder):
        summary = await run_crypto_scan(bars_fetcher=_fetcher(_bars()))

    assert summary["skipped_unrepresentable_price"] == 1
    assert summary["routable"] == 0
    assert summary["signals"] == 1          # still reported, not dropped
    recorder.assert_not_awaited()

    from app.services.crypto_scan import recent_crypto_signals
    latest = recent_crypto_signals(1)[0]
    assert latest["routable"] is False
    assert "price_precision_guard" in latest["reasons"]


@pytest.mark.asyncio
async def test_scan_survives_a_data_failure(one_symbol, no_account_call):
    """One symbol's fetch blowing up must not take the scan (or the scheduler) down."""
    async def _boom(symbol: str, limit: int = 250):
        raise RuntimeError("yfinance is having a day")

    summary = await run_crypto_scan(bars_fetcher=_boom)
    assert summary["errors"] == 1
    assert summary["signals"] == 0


@pytest.mark.asyncio
async def test_scan_below_confidence_floor_is_not_recorded(one_symbol, no_account_call, fake_indicators):
    recorder = AsyncMock()
    with patch("app.services.equity_signal_engine.score_equity_signal",
               return_value=("BUY", 0.49, {})), \
         patch("app.services.signal_outcome_tracker.record_signal", recorder):
        summary = await run_crypto_scan(bars_fetcher=_fetcher(_bars()))

    assert summary["signals"] == 1
    assert summary["routable"] == 0
    recorder.assert_not_awaited()


# ── record_signal: asset_type threading ──────────────────────────────────────

def _mock_session(existing_id=None):
    """
    record_signal's session fake, autobegin included — see
    tests/test_signal_outcome_tracker.py::_mock_session for why that matters.
    Also captures the statements executed so the dedup filter can be inspected.
    """
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.statements = []

    state = {"in_transaction": False}

    def _begin():
        if state["in_transaction"]:
            raise InvalidRequestError("A transaction is already begun on this Session.")
        state["in_transaction"] = True
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    async def _execute(stmt, *_a, **_k):
        state["in_transaction"] = True
        session.statements.append(stmt)
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


def _crypto_signal(**over) -> dict:
    signal = {
        "id": "sig-c1", "ticker": "BTC-USD", "asset_type": "crypto",
        "action": "BUY", "confidence": 0.81,
        "regime": CRYPTO_REGIME_UNCLASSIFIED,
        "signal_engine_version": CRYPTO_SCORING_VERSION,
        "generated_at": "2026-01-05T14:30:00+00:00",
        "trade_plan": {"entry_price": 110_000.0, "stop_price": 104_500.0,
                       "target_price": 121_000.0, "target_move_pct": 10.0},
        "indicators": {"rsi": 58.0, "macd": 120.0, "bb_pct_b": 0.7,
                       "atr": 2_500.0, "volume_ratio": 1.4},
    }
    signal.update(over)
    return signal


@pytest.mark.asyncio
async def test_record_signal_stores_crypto_asset_type_and_version():
    session = _mock_session()
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        row_id = await record_signal(_crypto_signal())

    assert row_id is not None
    inserted = session.add.call_args.args[0]
    assert inserted.asset_type == "crypto"
    assert inserted.ticker == "BTC-USD"
    assert inserted.signal_engine_version == CRYPTO_SCORING_VERSION
    assert inserted.regime == CRYPTO_REGIME_UNCLASSIFIED
    assert float(inserted.entry_price) == 110_000.0


@pytest.mark.asyncio
async def test_record_signal_dedup_filters_on_the_signals_asset_type():
    """
    The dedup SELECT must filter on the SAME asset_type the insert writes.
    Asserted against the compiled SQL because the bug this prevents is
    invisible in the inserted row.
    """
    session = _mock_session()
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        await record_signal(_crypto_signal())

    assert session.statements, "dedup lookup did not run"
    sql = str(session.statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "'crypto'" in sql
    assert "'equity'" not in sql


@pytest.mark.asyncio
async def test_record_signal_still_defaults_to_equity():
    """The pre-existing equity caller passes no asset_type and must be unaffected."""
    from app.services.equity_signal_engine import EQUITY_SCORING_VERSION

    session = _mock_session()
    signal = _crypto_signal(ticker="NVDA", asset_type=None,
                            signal_engine_version=None, regime="low_vol_trending")
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        await record_signal(signal)

    inserted = session.add.call_args.args[0]
    assert inserted.asset_type == "equity"
    assert inserted.signal_engine_version == EQUITY_SCORING_VERSION
    sql = str(session.statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "'equity'" in sql


@pytest.mark.asyncio
async def test_record_signal_dedups_a_repeat_crypto_signal():
    """Second pass over the same (symbol, action, UTC day) keeps the first row."""
    session = _mock_session(existing_id="existing-row")
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        row_id = await record_signal(_crypto_signal())

    assert row_id == "existing-row"
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_record_signal_treats_a_lost_insert_race_as_a_dedup_hit():
    """
    Raised in review on #84: the SELECT-then-INSERT dedup is not atomic, and
    phase 1 added a second way to overlap (POST /api/crypto/scan alongside the
    scheduler). Migration 0035 adds a partial unique index for crypto, so the
    loser of a race now gets an IntegrityError on commit.

    Losing is a dedup HIT, not a failure: the other writer recorded the same
    signal, and this function's contract is "first one wins, return its id".
    Returning None here would make the scan log a phantom failure for a row that
    exists, and returning the would-be id would hand back a row that does not.
    """
    session = _mock_session()
    winner = uuid.uuid4()

    calls = {"n": 0}

    async def _execute(stmt, *_a, **_k):
        calls["n"] += 1
        session.statements.append(stmt)
        lookup = MagicMock()
        # First lookup: nothing yet (so the insert is attempted). Second: the
        # race winner, found after the conflict.
        lookup.scalar_one_or_none = MagicMock(
            return_value=None if calls["n"] == 1 else winner
        )
        return lookup

    session.execute = AsyncMock(side_effect=_execute)
    session.commit = AsyncMock(side_effect=IntegrityError("dup", None, Exception()))
    session.rollback = AsyncMock()

    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        row_id = await record_signal(_crypto_signal())

    assert row_id == str(winner)
    session.rollback.assert_awaited()


@pytest.mark.asyncio
async def test_record_signal_returns_none_if_the_race_winner_cannot_be_found():
    """A conflict with no findable winner is a genuine anomaly — say nothing was
    recorded rather than invent an id."""
    session = _mock_session()

    async def _execute(stmt, *_a, **_k):
        session.statements.append(stmt)
        lookup = MagicMock()
        lookup.scalar_one_or_none = MagicMock(return_value=None)
        return lookup

    session.execute = AsyncMock(side_effect=_execute)
    session.commit = AsyncMock(side_effect=IntegrityError("dup", None, Exception()))
    session.rollback = AsyncMock()

    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        assert await record_signal(_crypto_signal()) is None
