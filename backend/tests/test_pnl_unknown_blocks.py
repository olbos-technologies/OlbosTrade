"""
An unreadable P&L history blocks; it does not read as zero.

`/api/guardrails/status` reads daily, weekly and monthly P&L plus the
consecutive-loss and trades-today counters from the database, and on ANY
exception used to substitute zeros and carry on. Zero P&L with zero counters is
exactly what a quiet, profitable day looks like, so the daily-loss, weekly-loss,
monthly-loss, consecutive-loss and daily-trade-cap limits all PASSED during a
database outage.

It is the mirror image of the 2026-10-31 failure. There a failed ACCOUNT read
substituted STARTING_CAPITAL and fabricated an 81.82% drawdown that blocked the
desk. Here a failed P&L read substitutes zero and fabricates safety that lets
it trade. The second is the more dangerous direction, because nobody
investigates a limit that did not fire.

Also covered: the audit row. `_maybe_log_guardrail_event` wrote
`portfolio_value = portfolio.current_value`, and when the account read had
failed that is STARTING_CAPITAL standing in for a measurement — which is how
guardrail_events came to hold 50000.0000 on every fabricated-drawdown row. An
unknown value is now stored as NULL.
"""
import ast
import pathlib
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.guardrails import (
    ACCOUNT_VALUE_UNKNOWN_FLAG,
    PNL_UNKNOWN_FLAG,
    GuardrailEngine,
    PortfolioState,
)

MAIN = pathlib.Path(__file__).resolve().parents[1] / "app" / "main.py"


def _portfolio(**kw) -> PortfolioState:
    base = dict(
        current_value=100_000.0, starting_capital=100_000.0,
        daily_pnl=Decimal("0"), weekly_pnl=Decimal("0"), monthly_pnl=Decimal("0"),
        consecutive_losses=0, trades_today=0,
    )
    base.update(kw)
    return PortfolioState(**base)


# ── the engine ───────────────────────────────────────────────────────────────

def test_the_same_portfolio_passes_when_known_and_blocks_when_unknown():
    """
    The whole defect in one before/after. Zeros are a legitimate reading, so a
    portfolio of zeros must trade — and the identical portfolio, flagged as
    unread, must not.
    """
    engine = GuardrailEngine()
    assert engine.check_all(_portfolio()).trading_allowed is True
    assert engine.check_all(_portfolio(pnl_known=False)).trading_allowed is False


def test_it_names_the_cause_and_is_not_dressed_as_a_loss_limit():
    status = GuardrailEngine().check_all(_portfolio(pnl_known=False))
    assert PNL_UNKNOWN_FLAG in status.flags
    assert "unavailable" in status.reason.lower()
    assert "unknown, not zero" in status.reason
    for limit in ("daily loss limit", "weekly loss limit", "monthly loss limit"):
        assert limit not in status.reason.lower()


def test_it_reports_no_percentage_derived_from_the_placeholder_zeros():
    status = GuardrailEngine().check_all(_portfolio(pnl_known=False))
    assert status.daily_loss_pct == status.weekly_loss_pct == status.monthly_loss_pct == 0.0
    assert status.trading_mode == "suspended"


def test_a_known_pnl_is_unaffected_and_the_flag_defaults_true():
    assert _portfolio().pnl_known is True
    status = GuardrailEngine().check_all(_portfolio())
    assert PNL_UNKNOWN_FLAG not in status.flags


def test_an_unknown_account_value_takes_precedence():
    """Both unreadable: report the one the percentages depend on."""
    status = GuardrailEngine().check_all(
        _portfolio(account_value_known=False, pnl_known=False)
    )
    assert ACCOUNT_VALUE_UNKNOWN_FLAG in status.flags
    assert PNL_UNKNOWN_FLAG not in status.flags


def test_an_active_cooling_off_is_still_reported_alongside_the_block():
    from datetime import datetime, timedelta, timezone
    until = datetime.now(timezone.utc) + timedelta(hours=6)
    status = GuardrailEngine().check_all(_portfolio(pnl_known=False, cooling_off_until=until))
    assert status.trading_allowed is False
    assert status.cooling_off_until == until


# ── the route ────────────────────────────────────────────────────────────────

def _pnl_handler() -> ast.ExceptHandler:
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        for stmt in ast.walk(node):
            if (
                isinstance(stmt, ast.Assign)
                and any(getattr(t, "id", None) == "pnl_known" for t in stmt.targets)
                and isinstance(stmt.value, ast.Constant) and stmt.value.value is False
            ):
                return node
    raise AssertionError("no except handler sets pnl_known = False")


def test_the_pnl_failure_handler_marks_unknown_binds_and_logs():
    handler = _pnl_handler()
    assert handler.name, "the handler discards the exception, so no cause can be logged"
    warned = [
        n for n in ast.walk(handler)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "warning" and getattr(n.func.value, "id", None) == "logger"
    ]
    assert warned, "the P&L failure handler does not log"
    src = ast.unparse(handler)
    assert "type(exc).__name__" in src and "[:200]" in src
    for call in ast.walk(handler):
        if isinstance(call, ast.Call):
            assert all(kw.arg != "exc_info" for kw in call.keywords), (
                "no traceback: this route is polled many times a minute"
            )


def test_pnl_known_is_handed_to_the_engine():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    passed = [
        kw for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "PortfolioState"
        for kw in node.keywords if kw.arg == "pnl_known"
    ]
    assert passed, "the route computes pnl_known but never passes it to PortfolioState"


def test_the_new_event_types_are_known_to_the_restart_seeding():
    src = MAIN.read_text(encoding="utf-8")
    block = src[src.index("_SUSPEND_EVENT_TYPES = {"):]
    block = block[: block.index("}")]
    for event in ("account_value_unknown", "pnl_unknown"):
        assert f'"{event}"' in block, (
            f"{event} is not in _SUSPEND_EVENT_TYPES: after a restart the seeded "
            "signature reads 'normal' for a block, and the next poll writes a duplicate"
        )


# ── the audit row ────────────────────────────────────────────────────────────

class _FakeSession:
    def __init__(self):
        self.added = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, _stmt):
        result = MagicMock()
        result.scalars.return_value.first.return_value = None
        return result

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        return None


async def _persisted_event(portfolio: PortfolioState):
    import app.main as main

    session = _FakeSession()
    status = GuardrailEngine().check_all(portfolio)
    previous = main._last_guardrail_signature
    main._last_guardrail_signature = ("normal", None)       # force a transition
    try:
        with patch("app.core.database.AsyncSessionLocal", lambda: session):
            await main._maybe_log_guardrail_event(status, portfolio)
    finally:
        main._last_guardrail_signature = previous
    assert session.added, "no GuardrailEvent was written — the transition was not recorded"
    return session.added[0]


@pytest.mark.asyncio
async def test_an_unknown_account_value_is_stored_as_null_not_as_the_substituted_constant():
    event = await _persisted_event(
        _portfolio(account_value_known=False, current_value=244_861.0)
    )
    assert event.event_type == ACCOUNT_VALUE_UNKNOWN_FLAG
    assert event.portfolio_value is None, (
        "the audit row records STARTING_CAPITAL as though it were the book — the "
        "same substitution that produced 50000.0000 on every fabricated-drawdown row"
    )


@pytest.mark.asyncio
async def test_a_measured_account_value_is_still_stored():
    event = await _persisted_event(_portfolio(pnl_known=False, current_value=123_456.0))
    assert event.event_type == PNL_UNKNOWN_FLAG
    assert event.portfolio_value == Decimal("123456.0")
