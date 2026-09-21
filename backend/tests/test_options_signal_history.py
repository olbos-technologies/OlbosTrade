"""Tests for options_signal_history — persistence of qualifying options spread signals."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import InvalidRequestError

from app.services.options_signal_history import record_options_signal


def _signal(**overrides) -> dict:
    base = {
        "id": "sig-1", "ticker": "SPY", "strategy": "bull_put_spread",
        "action": "SELL_SPREAD", "confidence": 0.82, "pop": 0.78,
        "kelly_fraction": 0.12, "signal_score": 0.65, "quantity": 2,
        "iv_rank": 45.0, "regime": "normal_mean_revert",
        "generated_at": "2026-08-16T14:30:00+00:00",
        "evidence": {"top_positive_factors": [], "top_negative_factors": []},
        "intelligence": {"pop": 0.78, "delta_short": -0.2},
        "spread": {
            "option_type": "put", "short_strike": 495.0, "long_strike": 490.0,
            "expiration": "2026-09-19", "dte": 34, "net_credit": 1.50,
            "max_loss": 3.50, "breakeven": 493.5,
        },
        "sigma": 0.18, "vix_used": 16.5, "credit_source": "ibkr",
    }
    base.update(overrides)
    return base


def _session(existing_id=None):
    """
    A mocked AsyncSession for record_options_signal().

    `existing_id` is what the dedup lookup finds: None means nothing has been
    recorded yet for this (ticker, strategy, action, day), so the insert runs.

    THIS FAKE AUTOBEGINS. The previous version returned an unconditional
    MagicMock from begin(), which never raised however many times it was
    called — so record_options_signal's `async with session.begin()` after the
    dedup SELECT looked fine here while a real AsyncSession raised
    InvalidRequestError on it, every single time. Nothing was ever written to
    options_signal_history, and these tests passed throughout.

    Same bug, same cause and same fix as signal_outcome_tracker; a fake more
    permissive than the database certifies the bug instead of catching it.
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


@pytest.mark.asyncio
async def test_record_options_signal_skips_duplicate_same_strategy_day():
    """The 30-minute scan re-emits a still-qualifying spread — keep only the first."""
    session = _session(existing_id="already-there")
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        result = await record_options_signal(_signal())

    session.add.assert_not_called()
    assert result == "already-there"


@pytest.mark.asyncio
async def test_record_options_signal_different_strategy_same_day_is_not_a_duplicate():
    """A bull put and an iron condor on SPY the same day are distinct signals."""
    session = _session()
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        result = await record_options_signal(_signal(strategy="iron_condor"))

    session.add.assert_called_once()
    assert result is not None


@pytest.mark.asyncio
async def test_record_options_signal_dedup_ignores_strikes():
    """
    Strikes drift with spot between ticks. If they were part of the identity
    the dedup would match almost nothing, so the lookup must not reference them.
    """
    session = _session()
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        await record_options_signal(_signal())

    stmt = str(session.execute.call_args.args[0])
    assert "short_strike" not in stmt and "long_strike" not in stmt
    assert "ticker" in stmt and "strategy" in stmt and "action" in stmt
    assert "generated_at >=" in stmt and "generated_at <" in stmt


@pytest.mark.asyncio
async def test_record_options_signal_noop_for_hold():
    result = await record_options_signal({"action": "HOLD", "ticker": "SPY"})
    assert result is None


@pytest.mark.asyncio
async def test_record_options_signal_noop_without_spread():
    result = await record_options_signal({"action": "SELL_SPREAD", "ticker": "SPY", "spread": {}})
    assert result is None


@pytest.mark.asyncio
async def test_record_options_signal_inserts_row():
    session = _session()
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        result = await record_options_signal(_signal())

    assert result is not None
    assert session.add.call_count == 1
    inserted = session.add.call_args.args[0]
    assert inserted.ticker == "SPY"
    assert inserted.strategy == "bull_put_spread"
    assert inserted.action == "SELL_SPREAD"
    assert float(inserted.pop) == 0.78
    assert float(inserted.kelly_fraction) == 0.12
    assert inserted.option_type == "put"
    assert float(inserted.short_strike) == 495.0
    assert float(inserted.long_strike) == 490.0
    assert inserted.dte == 34
    assert float(inserted.net_credit) == 1.50
    assert inserted.evidence == {"top_positive_factors": [], "top_negative_factors": []}
    assert inserted.intelligence == {"pop": 0.78, "delta_short": -0.2}


@pytest.mark.asyncio
async def test_record_options_signal_debit_spread_nulls_pop_and_kelly():
    """Matches app.main's own comment: analyze_spread() is skipped for debit
    spreads, so pop/kelly_fraction/intelligence are genuinely None there —
    the row must carry that absence honestly, not a fabricated 0."""
    session = _session()
    signal = _signal(action="BUY_SPREAD", pop=None, kelly_fraction=None, intelligence=None)
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        result = await record_options_signal(signal)

    assert result is not None
    inserted = session.add.call_args.args[0]
    assert inserted.action == "BUY_SPREAD"
    assert inserted.pop is None
    assert inserted.kelly_fraction is None
    assert inserted.intelligence is None


@pytest.mark.asyncio
async def test_record_options_signal_returns_none_on_db_failure():
    with patch("app.core.database.AsyncSessionLocal", side_effect=Exception("db down")):
        result = await record_options_signal(_signal())
    assert result is None


@pytest.mark.asyncio
async def test_record_options_signal_falls_back_to_now_on_bad_generated_at():
    session = _session()
    signal = _signal(generated_at="not-a-real-timestamp")
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        result = await record_options_signal(signal)

    assert result is not None
    inserted = session.add.call_args.args[0]
    assert inserted.generated_at is not None


@pytest.mark.asyncio
async def test_record_options_signal_nulls_unparseable_pop():
    """_dec_or_none must swallow a value it can't coerce to Decimal rather
    than raising, matching the equity precedent (record_signal's own
    _dec_or_none helper)."""
    session = _session()
    signal = _signal(pop="not-a-number")
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        result = await record_options_signal(signal)

    assert result is not None
    inserted = session.add.call_args.args[0]
    assert inserted.pop is None


# ── the commit the fix made load-bearing ─────────────────────────────────────

@pytest.mark.asyncio
async def test_record_options_signal_commits_the_inserted_row():
    """add() without commit() writes nothing.

    Removing the nested `async with session.begin()` also removed the implicit
    commit its context manager performed, so the commit is now explicit and
    load-bearing. Every other test here asserts only that session.add was
    called, and session.commit is an AsyncMock — so deleting the commit line
    would leave them all green on a function that stages a row and drops it.
    Raised in review on #80.
    """
    session = _session()
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        result = await record_options_signal(_signal())

    assert result is not None
    assert session.add.call_count == 1
    assert session.commit.await_count == 1, (
        "the options row was staged but never committed — nothing reaches "
        "the database"
    )


@pytest.mark.asyncio
async def test_record_options_signal_does_not_commit_a_duplicate():
    """The dedup path returns early, so it must write and commit nothing."""
    session = _session(existing_id="already-there")
    with patch("app.core.database.AsyncSessionLocal", return_value=session):
        result = await record_options_signal(_signal())

    assert result == "already-there"
    assert session.add.call_count == 0
    assert session.commit.await_count == 0
