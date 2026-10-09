"""
Execution Mode Manager — Manual / Copilot / Autopilot.

Manual   : Signals generated, NO orders sent. Review in Trade Desk.
Copilot  : Signals queued as pending approvals. User approves → order sent.
Autopilot: Signals pass guardrails → order sent immediately.

Persisted to the execution_events table (kind="mode_change") so mode survives
a restart. Previously written to /tmp/olbostrade_exec_mode.json inside the
container, wiped on every deploy — the same class of bug as the bracket-order
ack race and pending-order grace-period timer fixed earlier. Mirrors the kill
switch's own DB rehydrate() pattern (kill_switch.py).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class ExecutionMode(str, Enum):
    MANUAL    = "manual"
    COPILOT   = "copilot"
    AUTOPILOT = "autopilot"


# Ordering by how much the machine may do without a human. set_mode's
# persistence rules key off the DIRECTION of a change, so this has to be an
# explicit ordering rather than something inferred from the enum.
_RANK = {
    ExecutionMode.MANUAL: 0,
    ExecutionMode.COPILOT: 1,
    ExecutionMode.AUTOPILOT: 2,
}


class ExecutionModeManager:
    def __init__(self) -> None:
        self._mode = ExecutionMode.MANUAL
        self._changed_at = datetime.now(timezone.utc)
        self._changed_by = "default"
        # Set when a write failed. Kept so the state is auditable and visible
        # in the UI rather than living only in a log line nobody reads.
        self._persistence_error: str | None = None
        self._unconfirmed_reduction: ExecutionMode | None = None
        self._restore_note: str | None = None

    @property
    def mode(self) -> ExecutionMode:
        return self._mode

    async def _record(self, mode: ExecutionMode, by: str, at: datetime) -> None:
        """Append the decision to execution_events. Raises on failure."""
        from app.core.database import AsyncSessionLocal
        from app.models.execution_event import ExecutionEvent

        async with AsyncSessionLocal() as session:
            async with session.begin():
                session.add(ExecutionEvent(
                    kind="mode_change",
                    status=mode.value,
                    payload={
                        "mode": mode.value,
                        "changed_by": by,
                        "changed_at": at.isoformat(),
                    },
                ))

    async def set_mode(self, mode: ExecutionMode, by: str = "user") -> dict:
        """Change execution mode, with persistence semantics that differ by
        DIRECTION. This asymmetry is the whole point and must not be collapsed
        into one rule.

        ESCALATING (toward more automation) PERSISTS FIRST. Previously the
        runtime was mutated before the write was attempted and the write's
        failure was swallowed by `except: logger.error(...)`, so a database
        outage produced a live Autopilot and an ordinary success response. The
        machine was then trading automatically on a decision nothing had
        recorded, and a restart — reading the last *persisted* row — would put
        it back to whatever it had been before. Increasing automation now
        requires a durable record to exist first; if the write fails the mode
        does NOT change and the caller is told so.

        REDUCING (toward less automation) APPLIES IMMEDIATELY. Making a safety
        reduction wait for a database that may be the thing that is broken
        would be the same mistake wearing the opposite sign. Stopping must not
        be blocked by an outage, so the runtime drops first and the write is
        best-effort — but a failed write is recorded and surfaced rather than
        logged and forgotten, because an unrecorded reduction is exactly what
        a restart would silently undo.
        """
        prev = self._mode
        at = datetime.now(timezone.utc)
        escalating = _RANK[mode] > _RANK[prev]

        if escalating:
            try:
                await self._record(mode, by, at)
            except Exception as exc:
                # Deliberately NOT a success response, and the mode is unchanged.
                logger.error(
                    "Refusing to raise execution mode %s → %s: the decision could "
                    "not be recorded (%s)", prev.value, mode.value, exc,
                )
                self._persistence_error = str(exc)
                return self.summary(
                    requested=mode,
                    persistence="unavailable",
                    detail=(
                        f"{mode.value.title()} was not engaged: the decision could "
                        "not be recorded, and automation must not run on a decision "
                        "nothing durably holds."
                    ),
                )
            self._persistence_error = None
            self._unconfirmed_reduction = None
            self._mode, self._changed_at, self._changed_by = mode, at, by
            logger.info("Execution mode: %s → %s (by %s)", prev.value, mode.value, by)
            return self.summary(persistence="confirmed")

        # Reducing, or re-asserting the same mode: take effect now.
        self._mode, self._changed_at, self._changed_by = mode, at, by
        try:
            await self._record(mode, by, at)
        except Exception as exc:
            self._persistence_error = str(exc)
            self._unconfirmed_reduction = mode
            logger.critical(
                "Execution mode reduced %s → %s but the change was NOT recorded "
                "(%s). The reduction is in force now; a restart before it is "
                "recorded would read the older, more permissive row.",
                prev.value, mode.value, exc,
            )
            logger.info("Execution mode: %s → %s (by %s)", prev.value, mode.value, by)
            return self.summary(
                persistence="unconfirmed",
                detail=(
                    f"{mode.value.title()} is in force now, but could not be "
                    "recorded. Re-apply it once the database is reachable."
                ),
            )
        self._persistence_error = None
        self._unconfirmed_reduction = None
        logger.info("Execution mode: %s → %s (by %s)", prev.value, mode.value, by)
        return self.summary(persistence="confirmed")

    async def rehydrate(self) -> None:
        """
        Restore the last-set mode from the DB on startup. Defaults to MANUAL
        (the safe default) if there's no history or the read fails.
        """
        from app.core.database import AsyncSessionLocal
        from app.models.execution_event import ExecutionEvent
        from sqlalchemy import select

        try:
            async with AsyncSessionLocal() as session:
                row = (await session.execute(
                    select(ExecutionEvent)
                    .where(ExecutionEvent.kind == "mode_change")
                    .order_by(ExecutionEvent.created_at.desc())
                    .limit(1)
                )).scalar_one_or_none()
            if row is not None:
                recorded = ExecutionMode(row.status)
                self._changed_at = row.created_at
                self._changed_by = (row.payload or {}).get("changed_by", "restored")
                if recorded is ExecutionMode.AUTOPILOT:
                    # AUTOPILOT IS NOT AUTO-RESTORED, and this is deliberate.
                    # A reduction out of Autopilot that could not be written
                    # leaves the older, more permissive row as the newest one
                    # on disk. Restoring it would hand automation back to a
                    # machine a human had just taken it away from, silently,
                    # as a side effect of a restart. The information needed to
                    # tell that case apart from a legitimate Autopilot is
                    # precisely the information the failed write did not
                    # record, so it cannot be recovered — only not relied on.
                    # Copilot keeps every signal and still asks a human first.
                    self._mode = ExecutionMode.COPILOT
                    self._restore_note = (
                        "Autopilot was in force before restart and has been "
                        "restored as Copilot. Re-engage it explicitly."
                    )
                    logger.warning(
                        "ExecutionModeManager restored AUTOPILOT as COPILOT — "
                        "automation is not resumed across a restart without an "
                        "explicit decision"
                    )
                else:
                    self._mode = recorded
                    logger.info(
                        "ExecutionModeManager restored — mode: %s",
                        self._mode.value.upper(),
                    )
            else:
                logger.info("ExecutionModeManager initialized — mode: MANUAL (default, no history)")
        except Exception as exc:
            logger.error(
                "ExecutionModeManager rehydrate failed (defaulting to MANUAL): %s", exc
            )

    def summary(
        self,
        *,
        requested: "ExecutionMode | None" = None,
        persistence: str = "confirmed",
        detail: str | None = None,
    ) -> dict:
        """State of the mode, with durability stated rather than implied.

        `persistence` is one of:
          confirmed    — in force and durably recorded
          unavailable  — REQUESTED BUT NOT IN FORCE; the record failed, so the
                         escalation was refused and `mode` is still the old one
          unconfirmed  — in force now, but the record failed; a restart would
                         read an older, more permissive row
          stale        — restored from disk, not re-asserted since (see
                         restore_note)

        `requested` is only set when it differs from what is in force, so a UI
        can show "you asked for Autopilot and did not get it" instead of
        quietly displaying the old value as though nothing happened.
        """
        descriptions = {
            ExecutionMode.MANUAL:    "Signals only — no orders sent. Review in Trade Desk.",
            ExecutionMode.COPILOT:   "Signals queued for your approval. You approve → order executes.",
            ExecutionMode.AUTOPILOT: "Signals auto-execute through guardrails. Kill switch always active.",
        }
        if persistence == "confirmed" and self._restore_note:
            persistence = "stale"
        out = {
            "mode":        self._mode.value,
            "description": descriptions[self._mode],
            "changed_at":  self._changed_at.isoformat(),
            "changed_by":  self._changed_by,
            "auto_equity":   self._mode == ExecutionMode.AUTOPILOT,
            "auto_options":  self._mode == ExecutionMode.AUTOPILOT,
            "needs_approval": self._mode == ExecutionMode.COPILOT,
            # Durability, stated. Absent these a failed write was
            # indistinguishable from a successful one.
            "persistence":  persistence,
            "persisted":    persistence == "confirmed",
        }
        if requested is not None and requested is not self._mode:
            out["requested_mode"] = requested.value
        if detail:
            out["detail"] = detail
        if self._persistence_error:
            out["persistence_error"] = self._persistence_error
        if self._unconfirmed_reduction is not None:
            out["unconfirmed_reduction"] = self._unconfirmed_reduction.value
        if self._restore_note:
            out["restore_note"] = self._restore_note
        return out


# Singleton
execution_mode_manager = ExecutionModeManager()
