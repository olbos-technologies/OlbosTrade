"""
Master Kill Switch — OlbosTrade's emergency stop.

FIX #11: Fully implemented kill switch with:
  - Immediate scheduler pause (no new signals fire)
  - Cancel all open broker orders
  - Flatten all open positions at market
  - Persist GuardrailEvent to DB so state survives restart
  - Singleton pattern — one instance shared across the app

USAGE:
    from app.services.kill_switch import kill_switch_service
    await kill_switch_service.engage("daily loss limit exceeded")
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

from app.broker.broker_interface import BrokerInterface, SpreadLeg, SpreadOrder
from app.core.database import AsyncSessionLocal
from app.models.risk_state import GuardrailEvent
from app.utils.logger import get_logger
from decimal import Decimal

logger = get_logger(__name__)

# rehydrate() runs at start-up, when the database is the most likely component
# to not be ready yet. Retry before concluding the state is unreadable, so a
# slow boot does not halt trading; only a genuinely unreadable state does.
_REHYDRATE_ATTEMPTS = 3
_REHYDRATE_BACKOFF_SECONDS = 1.5


class KillSwitchError(Exception):
    """Raised when kill switch encounters a non-recoverable error."""
    pass


class KillSwitch:
    """
    FIX #11: Functional kill switch with full position flattening.

    Thread-safe via asyncio.Lock.
    Persists engaged state to DB so restarts don't re-enable trading.
    """

    def __init__(self) -> None:
        self._engaged = False
        self._engaged_at: Optional[datetime] = None
        self._reason: Optional[str] = None
        # True when engaged only because startup could not read the real state.
        # Surfaced in status() so an operator can tell a fail-closed halt apart
        # from one something actually triggered.
        self._rehydrate_unverified = False
        self._lock = asyncio.Lock()
        self._broker: Optional[BrokerInterface] = None
        self._scheduler = None  # injected after startup

    def configure(self, broker: BrokerInterface, scheduler=None) -> None:
        """Inject dependencies after startup (avoids circular imports)."""
        self._broker = broker
        self._scheduler = scheduler

    @property
    def is_engaged(self) -> bool:
        return self._engaged

    @property
    def status(self) -> dict:
        return {
            "engaged": self._engaged,
            "engaged_at": self._engaged_at.isoformat() if self._engaged_at else None,
            "reason": self._reason,
            "rehydrate_unverified": self._rehydrate_unverified,
        }

    async def engage(self, reason: str = "manual") -> dict:
        """
        Engage the kill switch.

        Steps (in order — each step is attempted even if prior step fails):
        1. Set engaged flag immediately (stops all new logic)
        2. Pause APScheduler (no new signal cycles)
        3. Cancel all open broker orders
        4. Flatten all open positions at market
        5. Persist GuardrailEvent to database

        Returns dict with results of each step.
        """
        async with self._lock:
            if self._engaged:
                logger.warning("Kill switch already engaged — ignoring duplicate call")
                return {"status": "already_engaged", "reason": self._reason}

            self._engaged = True
            self._engaged_at = datetime.now(timezone.utc)
            self._reason = reason

            logger.critical(
                "🛑 KILL SWITCH ENGAGED — reason: %s | time: %s",
                reason, self._engaged_at.isoformat(),
            )

        results: dict = {
            "reason": reason,
            "engaged_at": self._engaged_at.isoformat(),
            "scheduler_paused": False,
            "orders_cancelled": 0,
            "positions_flattened": 0,
            # Per-status tally of the closing orders. positions_flattened
            # counts every non-rejected result, so `submitted` (accepted, no
            # fill yet) and `partial` (residual exposure the caller MUST
            # handle) and `cancelled` all land in it. That number alone cannot
            # tell an operator whether the book is actually flat, which is the
            # only question that matters during a kill-switch event. Kept as-is
            # for the logs and the GuardrailEvent audit note; this is what the
            # UI should report.
            "flatten_statuses": {},
            "db_persisted": False,
            "errors": [],
        }

        # ── Step 1: Pause scheduler immediately ───────────────────────────
        try:
            if self._scheduler is not None:
                self._scheduler.pause()
                results["scheduler_paused"] = True
                logger.info("Kill switch: scheduler paused")
            else:
                results["errors"].append("scheduler_not_configured")
        except Exception as exc:
            results["errors"].append(f"scheduler_pause: {exc}")
            logger.error("Kill switch: failed to pause scheduler: %s", exc)

        # ── Step 2: Cancel all open orders ────────────────────────────────
        if self._broker is not None:
            try:
                # For IBKR: cancel all open orders
                if hasattr(self._broker, "ib"):
                    open_orders = self._broker.ib.openOrders()
                    for order in open_orders:
                        try:
                            self._broker.ib.cancelOrder(order)
                            results["orders_cancelled"] += 1
                        except Exception as exc:
                            results["errors"].append(f"cancel_order_{order.orderId}: {exc}")
                    logger.info(
                        "Kill switch: cancelled %d open orders",
                        results["orders_cancelled"],
                    )
            except Exception as exc:
                results["errors"].append(f"cancel_orders: {exc}")
                logger.error("Kill switch: failed to cancel orders: %s", exc)

            # ── Step 3: Flatten all open positions at market ───────────────
            try:
                positions = await self._broker.get_positions()
                logger.info(
                    "Kill switch: flattening %d positions", len(positions)
                )

                flatten_tasks = []
                for pos in positions:
                    # Skip already-flat positions (defensive — IBKR sometimes
                    # returns zero-quantity rows).
                    if pos.quantity == 0:
                        continue

                    close_action = "SELL" if pos.quantity > 0 else "BUY"

                    # Equity / ETF positions use the strike==0 sentinel from
                    # get_positions(). They MUST be flattened with a plain equity
                    # market order — sending them through an options combo would
                    # fail to qualify and leave the position naked.
                    if pos.strike == Decimal("0"):
                        flatten_tasks.append(
                            self._flatten_equity(pos, close_action, results)
                        )
                        continue

                    flatten_order = SpreadOrder(
                        strategy="kill_switch_flatten",
                        underlying=pos.underlying,
                        legs=[
                            SpreadLeg(
                                # Use the ROOT underlying symbol (e.g. "SPY"), not
                                # the OCC localSymbol — Option contracts are
                                # qualified from the root or qualification fails.
                                symbol=pos.underlying,
                                strike=pos.strike,
                                expiration=pos.expiration,
                                option_type=pos.option_type,
                                action=close_action,
                                quantity=abs(pos.quantity),
                            )
                        ],
                        limit_price=Decimal("0"),
                        order_type="MKT",  # market order — fill at any price
                        time_in_force="DAY",
                    )
                    flatten_tasks.append(
                        self._flatten_position(flatten_order, pos.symbol, results)
                    )

                # Run all flatten orders concurrently
                await asyncio.gather(*flatten_tasks, return_exceptions=True)

            except Exception as exc:
                results["errors"].append(f"get_positions: {exc}")
                logger.error("Kill switch: failed to fetch positions for flattening: %s", exc)
        else:
            results["errors"].append("broker_not_configured")
            logger.error("Kill switch: broker not configured — positions NOT flattened")

        # ── Step 4: Persist to database ────────────────────────────────────
        try:
            async with AsyncSessionLocal() as session:
                event = GuardrailEvent(
                    event_type="kill_switch",
                    notes=(
                        f"reason={reason} | "
                        f"orders_cancelled={results['orders_cancelled']} | "
                        f"positions_flattened={results['positions_flattened']} | "
                        f"errors={results['errors']}"
                    ),
                )
                session.add(event)
                await session.commit()
            results["db_persisted"] = True
            logger.info("Kill switch: event persisted to DB")
        except Exception as exc:
            results["errors"].append(f"db_persist: {exc}")
            logger.error("Kill switch: failed to persist to DB: %s", exc)

        # Summary log
        if results["errors"]:
            logger.critical(
                "Kill switch completed WITH ERRORS — manual review required. "
                "Errors: %s", results["errors"]
            )
        else:
            logger.info(
                "Kill switch complete — %d orders cancelled, %d positions flattened",
                results["orders_cancelled"], results["positions_flattened"],
            )

        return results

    async def _flatten_position(
        self, order: SpreadOrder, symbol: str, results: dict
    ) -> None:
        """Attempt to flatten a single options position, logging success/failure."""
        try:
            result = await self._broker.place_order(order)
            status = getattr(result, "status", None)
            if status == "rejected":
                msg = getattr(result, "message", "") or ""
                results["errors"].append(f"flatten_{symbol}: rejected {msg}".strip())
                logger.error("Kill switch: flatten REJECTED for %s — %s", symbol, msg)
                return
            results["positions_flattened"] += 1
            results["flatten_statuses"][str(status)] = (
                results["flatten_statuses"].get(str(status), 0) + 1
            )
            logger.info("Kill switch: flattened %s (status=%s)", symbol, status)
        except Exception as exc:
            results["errors"].append(f"flatten_{symbol}: {exc}")
            logger.error("Kill switch: failed to flatten %s: %s", symbol, exc)

    async def _flatten_equity(self, pos, close_action: str, results: dict) -> None:
        """Flatten a single equity/ETF position via a plain market order."""
        symbol = pos.symbol
        try:
            result = await self._broker.place_equity_order(
                ticker=symbol,
                qty=abs(pos.quantity),
                side=close_action,
                order_type="market",
            )
            status = getattr(result, "status", None)
            if status == "rejected":
                msg = getattr(result, "message", "") or ""
                results["errors"].append(f"flatten_equity_{symbol}: rejected {msg}".strip())
                logger.error("Kill switch: equity flatten REJECTED for %s — %s", symbol, msg)
                return
            results["positions_flattened"] += 1
            results["flatten_statuses"][str(status)] = (
                results["flatten_statuses"].get(str(status), 0) + 1
            )
            logger.info("Kill switch: flattened equity %s (status=%s)", symbol, status)
        except Exception as exc:
            results["errors"].append(f"flatten_equity_{symbol}: {exc}")
            logger.error("Kill switch: failed to flatten equity %s: %s", symbol, exc)

    async def reset(self, authorization_code: str = "") -> dict:
        """
        Reset kill switch after manual review.
        Requires authorization_code matching settings.kill_switch_reset_code.
        """
        from app.core.config import settings

        expected = (settings.kill_switch_reset_code or "").strip()
        if not expected:
            return {
                "reset": False,
                "reason": "Kill-switch reset is not configured. "
                          "Set KILL_SWITCH_RESET_CODE on the server.",
            }
        if not authorization_code or authorization_code != expected:
            return {
                "reset": False,
                "reason": "Invalid authorization code.",
            }

        async with self._lock:
            self._engaged = False
            self._engaged_at = None
            self._reason = None

        if self._scheduler is not None:
            self._scheduler.resume()

        # Persist the reset so a restart does NOT re-engage from the prior
        # engage event (rehydrate() reads the most recent event of either type).
        try:
            async with AsyncSessionLocal() as session:
                session.add(GuardrailEvent(
                    event_type="kill_switch_reset",
                    notes="manual reset via authorization_code",
                ))
                await session.commit()
        except Exception as exc:
            logger.error("Kill switch: failed to persist reset event: %s", exc)

        logger.warning("Kill switch RESET — trading re-enabled after manual review")
        return {"reset": True, "trading_enabled": True}

    async def rehydrate(self) -> None:
        """
        Restore engaged state from the DB on startup.

        The kill switch is a safety control — if it was engaged when the process
        died/restarted, it MUST come back engaged, or a restart would silently
        re-enable trading. Reads the most recent kill_switch / kill_switch_reset
        event: if the latest is an engage with no later reset, re-engage (flag
        only — positions were already flattened when it first engaged).
        """
        try:
            from sqlalchemy import select

            # Retry before concluding the state is unreadable. rehydrate() runs
            # during start-up, where the database is the single most likely
            # thing to not be ready yet; failing closed on the first blip would
            # halt trading every slow boot.
            row = None
            last_exc: Optional[Exception] = None
            for attempt in range(_REHYDRATE_ATTEMPTS):
                try:
                    async with AsyncSessionLocal() as session:
                        row = (await session.execute(
                            select(GuardrailEvent)
                            .where(GuardrailEvent.event_type.in_(("kill_switch", "kill_switch_reset")))
                            .order_by(GuardrailEvent.timestamp.desc())
                            .limit(1)
                        )).scalar_one_or_none()
                    last_exc = None
                    break
                except Exception as exc:  # noqa: PERF203 - retry is the point
                    last_exc = exc
                    if attempt < _REHYDRATE_ATTEMPTS - 1:
                        logger.warning(
                            "Kill switch rehydrate attempt %d/%d failed, retrying: %s",
                            attempt + 1, _REHYDRATE_ATTEMPTS, exc,
                        )
                        await asyncio.sleep(_REHYDRATE_BACKOFF_SECONDS * (attempt + 1))
            if last_exc is not None:
                raise last_exc
            if row is not None and row.event_type == "kill_switch":
                async with self._lock:
                    self._engaged = True
                    self._engaged_at = row.timestamp
                    self._reason = "restored as engaged after restart"
                logger.critical(
                    "🛑 Kill switch RESTORED as ENGAGED from DB (last engage at %s) — "
                    "trading stays halted until a manual reset.",
                    row.timestamp.isoformat() if row.timestamp else "unknown",
                )
        except Exception as exc:
            # Fail CLOSED. The docstring above states the contract this used to
            # break: "if it was engaged when the process died/restarted, it MUST
            # come back engaged, or a restart would silently re-enable trading."
            # The previous behaviour here was to log and leave _engaged False —
            # which is exactly that silent re-enable, reached by nothing more
            # exotic than the DB not accepting connections yet during container
            # start-up ordering.
            #
            # An unreadable state is not evidence of a clear switch, so it is
            # treated as engaged. The asymmetry is deliberate and cheap: a
            # spurious halt costs an operator one deliberate reset, while a
            # spurious resume re-enables trading after a halt that something
            # thought was necessary. The app cannot trade without the DB
            # anyway — guardrails, trade recording and the portfolio gate all
            # read it — so halting on an unreadable DB forbids nothing that
            # would otherwise have worked.
            async with self._lock:
                self._engaged = True
                self._engaged_at = datetime.now(timezone.utc)
                self._reason = "engaged state could not be read at startup — failing closed"
                self._rehydrate_unverified = True
            logger.critical(
                "🛑 Kill switch state UNREADABLE at startup (%s) — failing closed, "
                "trading halted until a manual reset. This is not necessarily a real "
                "engage: the DB read failed after %d attempts.", exc, _REHYDRATE_ATTEMPTS,
            )


# ── Singleton instance ────────────────────────────────────────────────────────
# Import this instance everywhere — do not instantiate KillSwitch directly
kill_switch_service = KillSwitch()
