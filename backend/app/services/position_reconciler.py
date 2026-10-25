"""
Position Reconciler — ensures OlbosTrade's DB state matches broker truth.

FIX #10: On every startup and before every signal cycle, compare broker
positions against DB open trades. Any discrepancy halts trading until
manually resolved to prevent position doubling on restart.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.broker.broker_interface import BrokerInterface, Position
from app.core.database import AsyncSessionLocal
from app.models.trade import Trade
from app.utils.logger import get_logger

logger = get_logger(__name__)


class ReconciliationError(Exception):
    """Raised when broker positions don't match DB state — halts trading."""

    def __init__(self, message: str, result: Optional["ReconciliationResult"] = None):
        super().__init__(message)
        self.result = result


@dataclass
class ReconciliationResult:
    """Result of a position reconciliation check."""
    clean: bool
    broker_position_count: int
    db_open_trade_count: int
    untracked_at_broker: list[str]   # in broker, not in DB — ghost positions
    phantom_in_db: list[str]         # in DB as open, not at broker — orphaned records
    quantity_mismatches: list[str] = field(default_factory=list)
    # Bare tickers with a quantity disagreement, populated only by check()
    # (reconcile()'s own quantity_mismatches field holds full warning
    # strings instead) — main.py's auto-correct step needs plain tickers,
    # not prose, to look the position back up.
    quantity_mismatch_tickers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class PositionReconciler:
    """
    FIX #10: Reconciles broker positions against DB on startup and before cycles.

    Design principle: the broker is always the source of truth.
    If they disagree, halt trading and require manual resolution.
    """

    def __init__(self, broker: BrokerInterface, *,
                 organization_id: "Optional[uuid.UUID]") -> None:
        """A broker paired with the owner whose trades it is authoritative for.

        `organization_id` is a REQUIRED keyword, which is the whole point of
        Batch E2 step 6. This class used to take one broker and compare it
        against every open trade in the database. With two tenants that is
        wrong in both directions: tenant A's broker knows nothing about tenant
        B's positions, so B's real trades come back as `phantom_in_db` and A's
        as `untracked_at_broker` — and either can drive an adopt or halt
        trading on data the broker was never asked about. Pairing them is the
        only way the two outputs mean anything.

        `None` is the single-operator scope: the process broker against the
        rows migration 0041 left unattributed. It must be passed explicitly.
        There is deliberately no default, so `PositionReconciler(broker)` no
        longer constructs — an unscoped reconciliation cannot be written by
        accident, only chosen in words.

        Resolving the pair is `worker_scope.broker_for_scope`'s job, not this
        class's: a class that looked up its own broker could be constructed
        for one owner and silently read another's.
        """
        self.broker = broker
        self.organization_id = organization_id

    @property
    def _owned(self):
        """This reconciler's ownership filter, so the three reads cannot drift."""
        from app.services.trade_scope import owned_by

        return owned_by(self.organization_id)

    @property
    def scope_label(self) -> str:
        """For logs and snapshot rows — a reconciliation result is meaningless
        without saying whose book it describes."""
        return "single-operator" if self.organization_id is None else str(self.organization_id)

    async def check(self) -> ReconciliationResult:
        """
        Non-raising reconciliation status for dashboards/monitoring.

        Same comparison as :meth:`reconcile` but never raises — untracked broker
        positions and DB phantoms are returned in the result for the UI to
        surface, rather than halting. ``clean`` is False when either side
        disagrees. On broker/DB error, returns a non-clean result with the error
        captured in ``warnings`` (fail-loud, but non-fatal).
        """
        from collections import defaultdict

        try:
            broker_positions = await self.broker.get_positions()
        except Exception as exc:
            return ReconciliationResult(
                clean=False, broker_position_count=0, db_open_trade_count=0,
                untracked_at_broker=[], phantom_in_db=[],
                warnings=[f"broker_unavailable: {exc}"],
            )

        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Trade).where(self._owned, Trade.status == "open")
                )
                db_open_trades = result.scalars().all()
        except Exception as exc:
            return ReconciliationResult(
                clean=False, broker_position_count=len(broker_positions),
                db_open_trade_count=0, untracked_at_broker=[], phantom_in_db=[],
                warnings=[f"db_unavailable: {exc}"],
            )

        broker_qty: dict[str, int] = defaultdict(int)
        for p in broker_positions:
            broker_qty[p.underlying] += abs(p.quantity)
        db_qty: dict[str, int] = defaultdict(int)
        for t in db_open_trades:
            db_qty[str(t.underlying)] += int(t.quantity or 1)

        untracked = sorted(set(broker_qty) - set(db_qty))
        phantom   = sorted(set(db_qty) - set(broker_qty))

        warnings = []
        mismatch_tickers = []
        for sym in sorted(set(broker_qty) & set(db_qty)):
            if broker_qty[sym] != db_qty[sym]:
                warnings.append(
                    f"QUANTITY MISMATCH {sym}: broker={broker_qty[sym]} db={db_qty[sym]}"
                )
                mismatch_tickers.append(sym)

        return ReconciliationResult(
            clean=not untracked and not phantom and not warnings,
            broker_position_count=len(broker_positions),
            db_open_trade_count=len(db_open_trades),
            untracked_at_broker=untracked,
            phantom_in_db=phantom,
            quantity_mismatch_tickers=mismatch_tickers,
            warnings=warnings,
        )

    async def check_contracts(self):
        """R2-A read-only signed contract check; never authorize entries from this.

        Legacy trades encode strategy-level spreads, not a verified execution-leg
        ledger. For now, nonempty DB trades are *unverified*, never inferred
        clean from matching ticker totals. This method deliberately does not
        change legacy reconcile()/check() or the live order path.
        """
        from app.services.contract_reconciliation import (
            ContractLeg, compare_contract_legs,
        )

        try:
            positions = await self.broker.get_positions()
        except Exception:
            return compare_contract_legs([], None)

        try:
            async with AsyncSessionLocal() as session:
                query = await session.execute(
                    select(Trade).where(self._owned, Trade.status == "open")
                )
                trades = query.scalars().all()
        except Exception:
            return compare_contract_legs([], None)

        # No safe row-by-row signed leg mapping exists yet for legacy spread
        # records; matching on underlying + quantity would be false evidence.
        if trades:
            return compare_contract_legs([], None)

        broker_legs = [
            ContractLeg(
                underlying=p.underlying, expiration=p.expiration,
                strike=p.strike, option_type=p.option_type, quantity=p.quantity,
            )
            for p in positions
            if p.quantity
        ]
        # An empty book on both sides is the only currently verified state.
        # Any broker holdings against no DB leg records are reported as drift.
        return compare_contract_legs(broker_legs, [])

    async def reconcile(self) -> ReconciliationResult:
        """
        Compare broker positions against DB open trades.

        Returns ReconciliationResult.
        Raises ReconciliationError if discrepancies are found — caller must
        handle this by halting the signal cycle.
        """
        # Fetch broker positions
        try:
            broker_positions: list[Position] = await self.broker.get_positions()
        except Exception as exc:
            raise ReconciliationError(
                f"Could not fetch broker positions for reconciliation: {exc}"
            ) from exc

        # Fetch DB open trades
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Trade).where(self._owned, Trade.status == "open")
            )
            db_open_trades: list[Trade] = result.scalars().all()

        # ── Quantity-aware matching ────────────────────────────────────────────
        # Group broker positions by underlying; sum absolute quantities.
        # A doubled position (qty=2 at broker, qty=1 in DB) is now detectable.
        from collections import defaultdict
        broker_qty: dict[str, int] = defaultdict(int)
        for p in broker_positions:
            broker_qty[p.underlying] += abs(p.quantity)

        db_qty: dict[str, int] = defaultdict(int)
        for t in db_open_trades:
            db_qty[str(t.underlying)] += int(t.quantity or 1)

        broker_underlyings = set(broker_qty.keys())
        db_underlyings     = set(db_qty.keys())

        untracked = broker_underlyings - db_underlyings
        phantom   = db_underlyings - broker_underlyings

        warnings: list[str] = []
        quantity_mismatches: list[str] = []

        # Quantity mismatches on known underlyings (e.g. doubled position)
        common = broker_underlyings & db_underlyings
        for sym in common:
            if broker_qty[sym] != db_qty[sym]:
                warn = (
                    f"QUANTITY MISMATCH for {sym}: broker={broker_qty[sym]} "
                    f"contracts, DB={db_qty[sym]}. Possible double-fill or partial close."
                )
                logger.critical(warn)
                warnings.append(warn)
                quantity_mismatches.append(warn)

        result = ReconciliationResult(
            # DB-only trades are drift too; warning-only does not mean clean.
            clean=not untracked and not phantom and not quantity_mismatches,
            broker_position_count=len(broker_positions),
            db_open_trade_count=len(db_open_trades),
            untracked_at_broker=list(untracked),
            phantom_in_db=list(phantom),
            quantity_mismatches=quantity_mismatches,
            warnings=warnings,
        )

        # Untracked broker positions — halt trading
        if untracked:
            msg = (
                f"RECONCILIATION FAILURE: {len(untracked)} positions at broker "
                f"have no matching DB record: {untracked}. "
                f"These are UNTRACKED positions — OlbosTrade does not know about them. "
                f"Trading halted until manual review."
            )
            logger.critical(msg)
            raise ReconciliationError(msg, result=result)

        # Quantity mismatches are also a hard halt
        if quantity_mismatches:
            raise ReconciliationError(
                f"Position quantity mismatch detected — trading halted: {quantity_mismatches}",
                result=result,
            )

        if phantom:
            warning = (
                f"WARNING: {len(phantom)} DB open trades have no matching broker position: "
                f"{phantom}. These may be stale records. Review and close if needed."
            )
            logger.warning(warning)
            warnings.append(warning)

        if result.clean:
            logger.info(
                "Position reconciliation clean — %d broker positions, %d DB open trades",
                result.broker_position_count, result.db_open_trade_count,
            )
        else:
            logger.warning(
                "Position reconciliation warnings: %s", warnings
            )

        return result

    async def reconcile_and_record(self, source: str = "system") -> ReconciliationResult:
        """
        Run reconciliation and persist the latest broker-vs-DB state.

        This provides a canonical, queryable status record for the operator
        terminal without changing the existing fail-closed behavior.
        """
        try:
            result = await self.reconcile()
        except ReconciliationError as exc:
            await self._persist_snapshot(
                result=exc.result,
                source=source,
                status="error",
                error_message=str(exc),
            )
            raise

        status = "warning" if result.warnings else "clean"
        await self._persist_snapshot(result=result, source=source, status=status)
        return result

    async def _persist_snapshot(
        self,
        *,
        result: Optional[ReconciliationResult],
        source: str,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        """
        Best-effort persistence of reconciliation state.

        Operational status should be visible in the terminal even when no one is
        tailing logs, but persistence failure must never mask the original drift.
        """
        try:
            from app.core.config import settings
            from app.models.reconciliation_snapshot import ReconciliationSnapshot

            checked_at = result.checked_at if result is not None else datetime.now(timezone.utc)

            snapshot = ReconciliationSnapshot(
                status=status,
                clean=bool(result.clean) if result is not None else False,
                source=source,
                broker_name=settings.broker,
                broker_position_count=result.broker_position_count if result is not None else 0,
                db_open_trade_count=result.db_open_trade_count if result is not None else 0,
                untracked_at_broker=list(result.untracked_at_broker) if result is not None else [],
                phantom_in_db=list(result.phantom_in_db) if result is not None else [],
                quantity_mismatches=list(result.quantity_mismatches) if result is not None else [],
                warnings=list(result.warnings) if result is not None else [],
                error_message=error_message,
                checked_at=checked_at,
            )

            async with AsyncSessionLocal() as session:
                session.add(snapshot)
                await session.commit()
        except Exception as exc:
            logger.error("Could not persist reconciliation snapshot: %s", exc)

    async def load_guardrail_state_from_db(self) -> dict:
        """
        FIX #12: Load persisted guardrail state from most recent portfolio snapshot.
        Prevents consecutive_losses and trades_today from resetting to 0 on restart.

        Returns dict compatible with PortfolioState constructor.
        """
        from app.core.config import settings
        from app.models.risk_state import PortfolioSnapshot

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(PortfolioSnapshot)
                .order_by(PortfolioSnapshot.timestamp.desc())
                .limit(1)
            )
            snap = result.scalar_one_or_none()

        from decimal import Decimal
        if snap is None:
            logger.info("No portfolio snapshot found — using default guardrail state")
            return {
                "current_value": settings.starting_capital,
                "starting_capital": settings.starting_capital,
                "daily_pnl":   Decimal("0"),
                "weekly_pnl":  Decimal("0"),
                "monthly_pnl": Decimal("0"),
                "consecutive_losses": 0,
                "trades_today": 0,
                "cooling_off_until": None,
            }

        logger.info(
            "Restored guardrail state from DB — "
            "consecutive_losses=%d trades_today=%d trading_mode=%s cooling_off_until=%s",
            snap.consecutive_losses, snap.trades_today,
            snap.trading_mode, snap.cooling_off_until,
        )

        from decimal import Decimal
        return {
            "current_value": float(snap.total_value),
            "starting_capital": settings.starting_capital,
            "daily_pnl":   Decimal(str(snap.daily_pnl   or 0)),
            "weekly_pnl":  Decimal(str(snap.weekly_pnl  or 0)),
            "monthly_pnl": Decimal(str(snap.monthly_pnl or 0)),
            "consecutive_losses": snap.consecutive_losses,
            "trades_today": snap.trades_today,
            "cooling_off_until": snap.cooling_off_until,
        }

    async def save_portfolio_snapshot(
        self,
        portfolio_value: float,
        daily_pnl: float,
        weekly_pnl: float,
        monthly_pnl: float,
        consecutive_losses: int,
        trades_today: int,
        trading_mode: str,
        cooling_off_until=None,
    ) -> None:
        """
        FIX #12: Persist current guardrail state to DB after every trade cycle.
        This is what survives restarts.
        """
        from app.models.risk_state import PortfolioSnapshot
        from app.core.config import settings

        from app.broker.ibkr_coordinator import Priority, ibkr_coordinator

        positions = await self.broker.get_positions()
        account = await ibkr_coordinator.submit(
            Priority.P0, self.broker.get_account_summary, key="account_summary", req_type="ACCOUNT_SUMMARY",
        )

        async with AsyncSessionLocal() as session:
            snap = PortfolioSnapshot(
                timestamp=datetime.now(timezone.utc),
                total_value=portfolio_value,
                cash=float(account.cash_balance),
                open_position_count=len(positions),
                daily_pnl=daily_pnl,
                weekly_pnl=weekly_pnl,
                monthly_pnl=monthly_pnl,
                trading_mode=trading_mode,
                consecutive_losses=consecutive_losses,
                trades_today=trades_today,
                cooling_off_until=cooling_off_until,
            )
            session.add(snap)
            await session.commit()

        logger.debug(
            "Portfolio snapshot saved — value=%.2f mode=%s consecutive_losses=%d",
            portfolio_value, trading_mode, consecutive_losses,
        )
