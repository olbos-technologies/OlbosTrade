"""
EmotionGuard's pause survives a restart — and nothing is written from a guess.

`save_portfolio_snapshot` was defined, documented as "what survives restarts",
and called from nowhere in the repository. The `portfolio_snapshots` table was
therefore empty for the life of the feature, EmotionGuard's `_persist_to_db()`
(which only UPDATES the latest row) silently did nothing, and every restart —
which is how this system is deployed — reset its eight-hour pause and its loss
count.

Three layers are pinned: the writer's refusals (the part that matters most),
EmotionGuard's loaded/merge behaviour, and the wiring. The wiring tests exist
because the original defect was precisely a correct function nobody called.
"""
import ast
import pathlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import guardrail_snapshot as gs
from app.services.guardrails import PortfolioState
from app.services.unified_risk import EmotionGuard

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
MAIN = APP / "main.py"


def _state(**kw) -> PortfolioState:
    base = dict(
        current_value=50_000.0,                       # a SUBSTITUTED constant on purpose
        starting_capital=50_000.0,
        daily_pnl=Decimal("-120.5"), weekly_pnl=Decimal("-300"), monthly_pnl=Decimal("40"),
        consecutive_losses=9,                         # DB-derived: must NOT be persisted
        trades_today=3,
    )
    base.update(kw)
    return PortfolioState(**base)


def _guard(consecutive=2, paused=None, loaded=True) -> EmotionGuard:
    g = EmotionGuard()
    g.state.consecutive_losses = consecutive
    g.state.paused_until = paused
    g.loaded = loaded
    return g


async def _run(guard, *, value=244_861.92, state=None, state_raises=None, save=None):
    save = save or AsyncMock()

    async def read_value():
        return value

    async def read_state():
        if state_raises:
            raise state_raises
        return state or _state()

    outcome = await gs.persist_guardrail_snapshot(
        emotion_guard=guard, read_account_value=read_value, read_state=read_state,
        evaluate_mode=lambda s: "normal", save=save,
    )
    return outcome, save


# ── the writer ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_fully_measured_cycle_is_saved():
    outcome, save = await _run(_guard())
    assert outcome == gs.SAVED
    save.assert_awaited_once()


@pytest.mark.asyncio
async def test_it_persists_the_measured_account_value_not_the_substituted_one():
    """The state reader hands back STARTING_CAPITAL as current_value on a failed read."""
    _, save = await _run(_guard(), value=244_861.92, state=_state(current_value=50_000.0))
    assert save.await_args.kwargs["portfolio_value"] == 244_861.92


@pytest.mark.asyncio
async def test_it_persists_the_guards_own_state_not_the_database_streak():
    """The two counts differ — the DB one includes adopted positions the guard never saw."""
    pause = datetime.now(timezone.utc) + timedelta(hours=5)
    _, save = await _run(_guard(consecutive=2, paused=pause), state=_state(consecutive_losses=9))
    kw = save.await_args.kwargs
    assert kw["consecutive_losses"] == 2
    assert kw["cooling_off_until"] == pause


@pytest.mark.asyncio
async def test_an_unmeasured_account_value_writes_nothing():
    outcome, save = await _run(_guard(), value=None)
    assert outcome == gs.SKIPPED_ACCOUNT_UNKNOWN
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unreadable_pnl_history_writes_nothing():
    outcome, save = await _run(_guard(), state_raises=RuntimeError("db down"))
    assert outcome == gs.SKIPPED_PNL_UNKNOWN
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_guard_that_never_loaded_is_not_written_from():
    """A real pause in the database must not be overwritten by a fresh-start guess."""
    g = _guard(loaded=False)
    g.rehydrate_from_db = AsyncMock(return_value=False)        # still failing
    outcome, save = await _run(g)
    assert outcome == gs.SKIPPED_GUARD_NOT_LOADED
    g.rehydrate_from_db.assert_awaited_once()
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_transient_startup_failure_recovers_on_a_later_run():
    g = _guard(loaded=False)

    async def _recover():
        g.loaded = True
        return True

    g.rehydrate_from_db = AsyncMock(side_effect=_recover)
    outcome, save = await _run(g)
    assert outcome == gs.SAVED
    save.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_failing_save_is_reported_not_raised():
    outcome, _ = await _run(_guard(), save=AsyncMock(side_effect=RuntimeError("insert failed")))
    assert outcome == gs.SKIPPED_SAVE_FAILED


# ── EmotionGuard ─────────────────────────────────────────────────────────────

def _db(row):
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    res = MagicMock()
    res.scalar_one_or_none = lambda: row
    session.execute = AsyncMock(return_value=res)
    session.commit = AsyncMock()
    return MagicMock(return_value=session), session


def test_a_fresh_guard_is_loaded_so_existing_behaviour_is_unchanged():
    assert EmotionGuard().loaded is True


@pytest.mark.asyncio
async def test_rehydrate_reports_success_and_failure():
    local, _ = _db(SimpleNamespace(consecutive_losses=3, cooling_off_until=None))
    g = EmotionGuard()
    with patch("app.core.database.AsyncSessionLocal", local):
        assert await g.rehydrate_from_db() is True
    assert g.loaded is True

    bad = EmotionGuard()
    with patch("app.core.database.AsyncSessionLocal", MagicMock(side_effect=Exception("down"))):
        assert await bad.rehydrate_from_db() is False
    assert bad.loaded is False


@pytest.mark.asyncio
async def test_a_failed_rehydrate_does_not_let_persist_overwrite_the_saved_row():
    local, session = _db(SimpleNamespace(consecutive_losses=5, cooling_off_until=None))
    g = EmotionGuard()
    with patch("app.core.database.AsyncSessionLocal", MagicMock(side_effect=Exception("down"))):
        await g.rehydrate_from_db()
    with patch("app.core.database.AsyncSessionLocal", local):
        await g._persist_to_db()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_rehydrate_keeps_the_more_restrictive_pause():
    later = datetime.now(timezone.utc) + timedelta(hours=6)
    earlier = datetime.now(timezone.utc) + timedelta(hours=1)

    g = EmotionGuard()
    g.state.paused_until = later
    local, _ = _db(SimpleNamespace(consecutive_losses=1, cooling_off_until=earlier))
    with patch("app.core.database.AsyncSessionLocal", local):
        await g.rehydrate_from_db()
    assert g.state.paused_until == later, "an in-memory pause must not be lost to an older stored one"

    g2 = EmotionGuard()
    local2, _ = _db(SimpleNamespace(consecutive_losses=1, cooling_off_until=later))
    with patch("app.core.database.AsyncSessionLocal", local2):
        await g2.rehydrate_from_db()
    assert g2.state.paused_until == later


@pytest.mark.asyncio
async def test_rehydrate_keeps_the_higher_loss_count():
    g = EmotionGuard()
    g.state.consecutive_losses = 4
    local, _ = _db(SimpleNamespace(consecutive_losses=2, cooling_off_until=None))
    with patch("app.core.database.AsyncSessionLocal", local):
        await g.rehydrate_from_db()
    assert g.state.consecutive_losses == 4


# ── the wiring ───────────────────────────────────────────────────────────────

def _main_tree() -> ast.Module:
    return ast.parse(MAIN.read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in main.py")


def test_the_scheduler_actually_calls_the_snapshot_job():
    """The original defect: a correct function that nothing called."""
    scheduler = _function(_main_tree(), "_background_scheduler")
    guarded_with_job = [
        n for n in ast.walk(scheduler)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_guarded"
        and any(
            isinstance(a, ast.Call) and getattr(a.func, "id", None) == "_persist_guardrail_snapshot"
            for a in n.args
        )
    ]
    assert guarded_with_job, (
        "_background_scheduler never runs _persist_guardrail_snapshot — portfolio_snapshots "
        "stays empty and EmotionGuard's pause is again lost on every restart"
    )


def test_save_portfolio_snapshot_has_a_caller():
    callers = []
    for path in APP.rglob("*.py"):
        if "save_portfolio_snapshot(" in path.read_text(encoding="utf-8"):
            callers.append(path.name)
    assert callers != ["position_reconciler.py"] and len(callers) >= 2, (
        f"save_portfolio_snapshot is defined and called from nowhere else: {callers}"
    )


def test_the_job_chooses_the_single_operator_scope_explicitly():
    job = _function(_main_tree(), "_persist_guardrail_snapshot")
    scoped = [
        kw for n in ast.walk(job)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "PositionReconciler"
        for kw in n.keywords if kw.arg == "organization_id"
    ]
    assert scoped and isinstance(scoped[0].value, ast.Constant) and scoped[0].value.value is None, (
        "PositionReconciler has no default scope on purpose; the desk-wide snapshot must name it"
    )


def test_the_measured_read_never_uses_the_substituting_helper():
    fn = _function(_main_tree(), "_read_measured_account_value")
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)
    }
    assert "get_account_value" not in names, (
        "get_account_value() returns STARTING_CAPITAL on failure; a snapshot must record "
        "a measurement or nothing"
    )


@pytest.mark.asyncio
async def test_the_measured_read_returns_none_on_failure_and_on_a_non_positive_value():
    import app.main as main

    async def _read(value=None, raises=False):
        broker = MagicMock()
        if raises:
            broker.get_account_summary = AsyncMock(side_effect=ConnectionError("gateway down"))
        else:
            broker.get_account_summary = AsyncMock(return_value=SimpleNamespace(net_liquidation=value))
        with patch("app.broker.broker_factory.get_broker", return_value=broker):
            return await main._read_measured_account_value()

    assert await _read(raises=True) is None
    assert await _read(value=0) is None
    assert await _read(value=-5) is None
    assert await _read(value=244_861.92) == 244_861.92


@pytest.mark.asyncio
async def test_the_mode_is_evaluated_against_the_measured_value_not_the_substituted_one():
    """
    The state reader fills current_value from get_account_value(), which returns
    STARTING_CAPITAL when the broker is unreachable. The mode stored in the row
    must come from the number that was actually measured.
    """
    seen = {}

    def _mode(state):
        seen["value"] = state.current_value
        return "normal"

    async def read_value():
        return 244_861.92

    async def read_state():
        return _state(current_value=50_000.0)

    await gs.persist_guardrail_snapshot(
        emotion_guard=_guard(), read_account_value=read_value, read_state=read_state,
        evaluate_mode=_mode, save=AsyncMock(),
    )
    assert seen["value"] == 244_861.92
