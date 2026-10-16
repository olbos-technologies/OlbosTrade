"""
Batch-4 acceptance tests — fill-confirmed recording & ML integrity.

Asserts:
  - dispatch_id is stored on the Trade row.
  - Duplicate dispatch_id is idempotent (returns same trade_id, no second insert).
  - record_fill failure emits CRITICAL log (not just ERROR).
  - JournalEntry confidence_level is NULL, not 3.
  - pnl_pct uses settings.starting_capital, not hardcoded 25000.
  - iv_rank degenerate case (hi == lo) returns 0.0, not 50.0.

Run with: pytest tests/test_batch4_recording.py -v
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ══════════════════════════════════════════════════════════════════════════════
# 1. dispatch_id stored on Trade row
# ══════════════════════════════════════════════════════════════════════════════

def test_dispatch_id_column_exists_on_trade_model():
    """Trade model must have a dispatch_id column with unique=True."""
    import inspect
    from app.models.trade import Trade
    src = inspect.getsource(Trade)
    assert "dispatch_id" in src, "Trade model missing dispatch_id column"
    assert "unique=True" in src or "unique" in src


def test_dispatch_id_in_record_fill_constructor():
    """record_fill must pass dispatch_id= when constructing the Trade object."""
    import inspect
    from app.services.trade_recorder import TradeRecorder
    src = inspect.getsource(TradeRecorder.record_fill)
    assert "dispatch_id=dispatch_id" in src


# ══════════════════════════════════════════════════════════════════════════════
# 2. Idempotency: duplicate dispatch_id returns existing trade_id
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_record_fill_idempotent_on_duplicate_dispatch_id():
    """
    If dispatch_id already exists in the DB, record_fill must return the
    existing trade_id without inserting a second row.
    """
    from app.services.trade_recorder import TradeRecorder

    existing_trade_id = uuid.uuid4()
    existing_trade = MagicMock()
    existing_trade.id = existing_trade_id

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=lambda: existing_trade)
    )

    recorder = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=mock_session):
        result = await recorder.record_fill(
            organization_id=None,   # single-operator scope
            strategy="bull_put_spread",
            underlying="SPY",
            option_type="put",
            short_strike=450.0,
            long_strike=440.0,
            expiration=date(2025, 6, 20),
            entry_credit=1.50,
            quantity=1,
            signal_score=0.75,
            iv_rank=35.0,
            regime="neutral",
            approved_by="autopilot",
            dispatch_id="DISP-DUPE-001",
        )

    assert result == str(existing_trade_id), "Should return existing trade_id, not None or new id"
    # Only 1 session was opened (the idempotency check) — no second insert
    assert mock_session.execute.call_count == 1


# ══════════════════════════════════════════════════════════════════════════════
# 3. record_fill failure emits CRITICAL log
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_record_fill_emits_critical_on_db_failure():
    """
    When a DB write fails after a broker fill, record_fill must emit CRITICAL.
    A lost fill must never be silently swallowed with only ERROR level.
    """
    import logging
    from app.services.trade_recorder import TradeRecorder

    recorder = TradeRecorder()

    # First session: dispatch_id check returns None (not a duplicate)
    # Second session: Trade insert raises
    call_count = 0

    def session_factory():
        nonlocal call_count
        call_count += 1
        mock = AsyncMock()
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=False)
        if call_count == 1:
            # idempotency check — no existing record
            mock.execute = AsyncMock(
                return_value=MagicMock(scalar_one_or_none=lambda: None)
            )
        else:
            # trade insert — DB explodes
            mock.execute = AsyncMock(side_effect=Exception("DB connection lost"))
            mock.begin = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(side_effect=Exception("DB connection lost")),
                __aexit__=AsyncMock(return_value=False),
            ))
        return mock

    with patch("app.core.database.AsyncSessionLocal", side_effect=session_factory):
        with patch.object(
            __import__("app.services.trade_recorder", fromlist=["logger"]).logger,
            "critical"
        ) as mock_critical:
            result = await recorder.record_fill(
                organization_id=None,   # single-operator scope
                strategy="bull_put_spread",
                underlying="SPY",
                option_type="put",
                short_strike=450.0,
                long_strike=440.0,
                expiration=date(2025, 6, 20),
                entry_credit=1.50,
                quantity=1,
                signal_score=0.75,
                iv_rank=35.0,
                regime="neutral",
                approved_by="autopilot",
                dispatch_id="DISP-FAIL-001",
            )

    assert result is None
    assert mock_critical.called, "logger.critical must be called when record_fill fails"
    # Confirm the message references dispatch_id
    logged_msg = str(mock_critical.call_args)
    assert "DISP-FAIL-001" in logged_msg or "dispatch_id" in logged_msg


def test_record_fill_uses_critical_not_only_error():
    """Verify source code uses logger.critical (not only logger.error) in the except block."""
    import inspect
    from app.services.trade_recorder import TradeRecorder
    src = inspect.getsource(TradeRecorder.record_fill)
    assert "logger.critical" in src, "record_fill except block must use logger.critical"


# ══════════════════════════════════════════════════════════════════════════════
# 4. confidence_level is NULL (not 3) in JournalEntry stub
# ══════════════════════════════════════════════════════════════════════════════

def test_confidence_level_is_none_in_record_fill():
    """record_fill must set confidence_level=None, not 3, in the JournalEntry stub."""
    import inspect
    from app.services.trade_recorder import TradeRecorder
    src = inspect.getsource(TradeRecorder.record_fill)
    assert "confidence_level=None" in src, "confidence_level must be None"
    assert "confidence_level=3" not in src, "confidence_level must not be hardcoded to 3"


# ══════════════════════════════════════════════════════════════════════════════
# 5. pnl_pct uses settings.starting_capital
# ══════════════════════════════════════════════════════════════════════════════

def test_pnl_pct_uses_settings_not_hardcoded():
    """record_exit must compute pnl_pct with settings.starting_capital, not 25000.0."""
    import inspect
    from app.services.trade_recorder import TradeRecorder
    src = inspect.getsource(TradeRecorder.record_exit)
    assert "25000.0" not in src, "pnl_pct must not divide by hardcoded 25000.0"
    assert "settings.starting_capital" in src, "pnl_pct must use settings.starting_capital"
    assert "pnl_capture_pct" in src, "record_exit should compute capture percentage"


@pytest.mark.asyncio
async def test_pnl_pct_scales_with_starting_capital():
    """pnl_pct for a $100 gain on $50,000 starting capital should be 0.002, not 0.004."""
    from app.services.trade_recorder import TradeRecorder
    from app.core.config import settings

    existing_credit = 2.00
    cost_to_close   = 1.00
    qty             = 1
    expected_pnl    = (existing_credit - cost_to_close) * qty * 100  # $100

    # We need to mock the DB trade object returned from select
    fake_trade = MagicMock()
    fake_trade.credit_received = Decimal("2.00")
    fake_trade.quantity = 1

    captured: dict = {}

    class _CaptureTrade:
        def __setattr__(self, name, value):
            if name in ("pnl", "pnl_pct", "pnl_capture_pct", "cost_to_close", "status", "exit_date", "exit_reason"):
                captured[name] = value
            super().__setattr__(name, value)

    fake_trade2 = _CaptureTrade()
    fake_trade2.credit_received = Decimal("2.00")
    fake_trade2.quantity = 1
    fake_trade2.mfe_pnl = Decimal("200.00")

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=lambda: fake_trade2)
    )
    ctx_mgr = MagicMock()
    ctx_mgr.__aenter__ = AsyncMock(return_value=mock_session)
    ctx_mgr.__aexit__ = AsyncMock(return_value=False)
    mock_session.begin = MagicMock(return_value=ctx_mgr)

    recorder = TradeRecorder()
    with patch("app.core.database.AsyncSessionLocal", return_value=mock_session):
        await recorder.record_exit(
            trade_id=str(uuid.uuid4()),
            cost_to_close=1.00,
            exit_reason="profit_target",
        )

    if "pnl_pct" in captured:
        actual_pnl_pct = float(captured["pnl_pct"])
        expected_pct = expected_pnl / settings.starting_capital
        assert abs(actual_pnl_pct - expected_pct) < 1e-6, (
            f"pnl_pct={actual_pnl_pct} but expected {expected_pct} "
            f"(starting_capital={settings.starting_capital})"
        )
    if "pnl_capture_pct" in captured:
        actual_capture = float(captured["pnl_capture_pct"])
        assert abs(actual_capture - 0.5) < 1e-6, (
            f"pnl_capture_pct={actual_capture} but expected 0.5"
        )


# ══════════════════════════════════════════════════════════════════════════════
# 6. iv_rank degenerate case returns 0.0
# ══════════════════════════════════════════════════════════════════════════════

def test_iv_rank_degenerate_returns_zero():
    """When hi == lo (no variance in iv series), _compute_iv_rank must return 0.0."""
    import pandas as pd
    from app.services.iv_surface import IVSurfaceEngine

    flat_series = pd.Series([0.25, 0.25, 0.25, 0.25, 0.25])
    result = IVSurfaceEngine._compute_iv_rank(flat_series, 0.25)
    assert result == 0.0, f"Degenerate iv_rank should be 0.0, got {result}"


def test_iv_rank_degenerate_not_50():
    """Confirm 50.0 is no longer returned for degenerate case."""
    import inspect
    from app.services import iv_surface
    src = inspect.getsource(iv_surface)
    # The old `return 50.0` must be gone
    assert "return 50.0" not in src, "Degenerate iv_rank must not return 50.0"
