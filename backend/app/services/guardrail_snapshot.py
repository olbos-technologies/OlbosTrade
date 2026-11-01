"""
Keep one portfolio_snapshots row current, so EmotionGuard's pause survives a restart.

EmotionGuard is a live gate: after four consecutive losses, or tilt, it blocks
entries for eight hours (trade_desk Stage 1a). It was written to survive a
process restart — rehydrate_from_db() restores it at startup, and the close
handler calls _persist_to_db() after every trade. Both update or read the latest
`portfolio_snapshots` row. Nothing ever INSERTED one, so the table has been empty
for the life of the feature, _persist_to_db() has silently done nothing, and
every restart reset the pause and the loss count. Restarting the backend is how
you deploy, so the pause was reset on every deploy.

This writes the row. Injected dependencies, not imports, so every branch can be
exercised without the application: the refusals below matter more than the
happy path.

WHAT IT WILL NOT WRITE, and why each refusal exists:

  * Anything from an EmotionGuard that was never loaded. A database that was
    down at startup leaves the guard holding defaults; a row written from them
    would sit on top of a real pause and make it the latest state.
  * Anything built on an unmeasured account value. total_value is mandatory in
    the table, and substituting STARTING_CAPITAL for a failed read is the
    defect that put 50000.0000 on every fabricated-drawdown row in
    guardrail_events. No measurement, no row.
  * Anything built on an unreadable P&L history, for the same reason.

The EmotionGuard fields (consecutive_losses, paused_until) are the guard's OWN
state, not the database-derived loss streak the status route computes. They are
different counts — one includes every closed trade including adopted positions,
the other only trades the guard was told about — and mixing them would make a
restart resume with a count the guard never had.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

SAVED = "saved"
SKIPPED_GUARD_NOT_LOADED = "skipped:emotion_guard_not_loaded"
SKIPPED_ACCOUNT_UNKNOWN = "skipped:account_value_unknown"
SKIPPED_PNL_UNKNOWN = "skipped:pnl_unknown"
SKIPPED_SAVE_FAILED = "skipped:save_failed"


async def persist_guardrail_snapshot(
    *,
    emotion_guard: Any,
    read_account_value: Callable[[], Awaitable[Optional[float]]],
    read_state: Callable[[], Awaitable[Any]],
    evaluate_mode: Callable[[Any], str],
    save: Callable[..., Awaitable[None]],
) -> str:
    """
    Write one snapshot if, and only if, every input was actually measured.
    Returns an outcome string; never raises, since a diagnostic-grade write
    must not be able to break the scheduler that calls it.
    """
    if not emotion_guard.loaded:
        # Retry the read. A transient failure at startup should not leave the
        # guard unable to persist for the rest of the process's life.
        await emotion_guard.rehydrate_from_db()
        if not emotion_guard.loaded:
            return SKIPPED_GUARD_NOT_LOADED

    value = await read_account_value()
    if value is None:
        return SKIPPED_ACCOUNT_UNKNOWN

    try:
        state = await read_state()
    except Exception as exc:
        logger.warning(
            "Guardrail snapshot skipped — P&L history unreadable (%s: %s)",
            type(exc).__name__, (str(exc) or "no message")[:200],
        )
        return SKIPPED_PNL_UNKNOWN

    # Use the MEASURED value, not whatever the state reader substituted.
    state = replace(state, current_value=value)

    try:
        await save(
            portfolio_value=value,
            daily_pnl=float(state.daily_pnl),
            weekly_pnl=float(state.weekly_pnl),
            monthly_pnl=float(state.monthly_pnl),
            consecutive_losses=emotion_guard.state.consecutive_losses,
            trades_today=state.trades_today,
            trading_mode=evaluate_mode(state),
            cooling_off_until=emotion_guard.state.paused_until,
            account_value_known=True,
        )
    except Exception as exc:
        logger.warning(
            "Guardrail snapshot not saved (%s: %s)",
            type(exc).__name__, (str(exc) or "no message")[:200],
        )
        return SKIPPED_SAVE_FAILED
    return SAVED
