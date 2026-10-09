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

import asyncio
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
# Retrying an unrecorded safety reduction. Starts quickly because a short
# database blip is the common case, and backs off so a long outage does not
# become a busy loop.
_RETRY_INITIAL_SECONDS = 2.0
_RETRY_MAX_SECONDS = 60.0
_RETRY_ATTEMPTS = 20


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
        self._retry_task: "asyncio.Task | None" = None

    @property
    def mode(self) -> ExecutionMode:
        return self._mode

    def _start_retry(self, mode: ExecutionMode, by: str, at: datetime) -> None:
        """Keep retrying an unrecorded safety reduction until it lands.

        Abandons the attempt if the mode moves on — a later change supersedes
        this one and writes its own row, and re-recording a stale reduction
        afterwards would make the history say the wrong thing.

        Best-effort by construction: if the process dies before the database
        comes back, nothing durable records that the reduction happened. That
        residual window is the known limit of restoring from the stored
        record, and is documented on rehydrate().
        """
        self._cancel_retry()

        async def _retry() -> None:
            delay = _RETRY_INITIAL_SECONDS
            for _ in range(_RETRY_ATTEMPTS):
                await asyncio.sleep(delay)
                if self._mode is not mode:
                    logger.info(
                        "Abandoning retry of unrecorded %s — mode has since "
                        "moved to %s", mode.value, self._mode.value,
                    )
                    return
                try:
                    await self._record(mode, by, at)
                except Exception as exc:
                    logger.warning(
                        "Retry of unrecorded execution mode %s failed: %s",
                        mode.value, exc,
                    )
                    delay = min(delay * 2, _RETRY_MAX_SECONDS)
                    continue
                self._persistence_error = None
                self._unconfirmed_reduction = None
                logger.info(
                    "Unrecorded execution mode %s has now been recorded", mode.value
                )
                return
            logger.critical(
                "Gave up recording execution mode %s after %d attempts — the "
                "stored history is still more permissive than the running mode",
                mode.value, _RETRY_ATTEMPTS,
            )

        try:
            self._retry_task = asyncio.get_running_loop().create_task(_retry())
        except RuntimeError:
            # No loop (synchronous caller, or a test). The reduction is still
            # in force; it simply will not be retried.
            self._retry_task = None

    def _cancel_retry(self) -> None:
        task, self._retry_task = self._retry_task, None
        if task is not None and not task.done():
            task.cancel()

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
            self._cancel_retry()
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
            # Keep trying in the background. This is what makes the stored
            # history trustworthy enough for rehydrate() to restore Autopilot
            # from: the window in which the newest row on disk is more
            # permissive than reality lasts until the database comes back,
            # not until someone notices.
            self._start_retry(mode, by, at)
            logger.critical(
                "Execution mode reduced %s → %s but the change was NOT recorded "
                "(%s). The reduction is in force now and will be retried; a "
                "restart before it lands would read the older, more permissive "
                "row.",
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
        self._cancel_retry()
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
                # The recorded mode is restored as recorded, Autopilot
                # included. An earlier revision refused to restore Autopilot
                # at all, which did close the hazard below but made every
                # deploy silently disarm automation — a cure that fires on
                # every restart for a fault that fires on almost none.
                #
                # What makes the record trustworthy is that a reduction which
                # fails to persist is now RETRIED until it lands, so the
                # window in which the newest row is more permissive than
                # reality ends when the database comes back rather than when
                # somebody notices.
                #
                # KNOWN RESIDUAL RISK, stated rather than designed around: if
                # the process dies during that window — database down, a
                # reduction applied in memory, no retry has landed yet —
                # nothing durable records the reduction, and this restores the
                # older Autopilot row. Recovering that would need the
                # information the failed write is precisely what did not
                # record. The kill switch, which persists separately and
                # rehydrates fail-closed, is the control that does not depend
                # on this path.
                self._mode = recorded
                if recorded is ExecutionMode.AUTOPILOT:
                    logger.warning(
                        "ExecutionModeManager restored AUTOPILOT — automation "
                        "resumes from the stored record; verify this is the "
                        "mode you expect"
                    )
                else:
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
        # Derive from state when the caller did not say. GET /execution-mode
        # calls summary() with no arguments, and defaulting to "confirmed"
        # there reported a healthy mode while an unrecorded reduction was
        # still outstanding — hiding exactly what these fields exist to show.
        if persistence == "confirmed":
            if self._unconfirmed_reduction is not None or self._persistence_error:
                persistence = "unconfirmed"
            elif self._restore_note:
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
