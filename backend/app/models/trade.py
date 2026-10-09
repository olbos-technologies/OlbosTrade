"""
SQLAlchemy model for the trades table.
Records every opened and closed options spread trade.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, DateTime, Enum, Index, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    strategy: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="bull_put_spread | bear_call_spread | iron_condor | bull_call_debit_spread"
    )
    # Who owns this position. NULLABLE and staying that way: a pre-ownership
    # trade's owner is not recoverable from the row (approved_by holds a role
    # label like "user" or "reconciler_adopt", never an identity), and nothing
    # links a trade to the broker_connections row that executed it. NOT NULL
    # would force migration 0041 to invent an owner for every historical trade.
    #
    # NULL is a SCOPE, not a wildcard: it is the single-operator scope, and
    # matches exactly the rows 0041 declined to guess an owner for. Querying it
    # as "no filter when None" would reinstate the shared query this removes —
    # see app/services/trade_scope.py.
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    underlying: Mapped[str] = mapped_column(String(10), nullable=False)
    spread_type: Mapped[str] = mapped_column(String(50), nullable=False)

    # Strikes
    short_strike: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    long_strike: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    short_strike_2: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    long_strike_2: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)

    expiration: Mapped[date] = mapped_column(Date, nullable=False)
    entry_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    exit_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    # Take-profit level as placed at entry. For equities the stop lives in
    # long_strike and the entry in credit_received/short_strike; the target
    # had no home until now, which made a target fill unprovable after the
    # fact. Null on every row written before migration 0028, and on any
    # entry that genuinely placed no profit leg.
    target_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)

    # P&L
    credit_received: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    cost_to_close: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    pnl: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4), nullable=True)
    pnl_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 6), nullable=True)
    mfe_pnl: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 4), nullable=True,
        comment="Maximum favorable excursion in dollars while the trade was open"
    )
    mae_pnl: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(12, 4), nullable=True,
        comment="Maximum adverse excursion in dollars while the trade was open"
    )
    pnl_capture_pct: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(8, 6), nullable=True,
        comment="Closed P&L divided by MFE, when MFE was positive"
    )

    # Excursions — best/worst unrealized P&L ($) seen while the trade was open.
    # Used to refine take-profit / stop-loss (how much was left on the table vs
    # how much heat the trade took before resolving).
    mfe: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4), nullable=True)
    mae: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 4), nullable=True)

    # Status
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open", index=True,
        comment="open | closed | expired"
    )
    exit_reason: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True,
        comment="profit_target | stop_loss | expiration | manual | dte_exit"
    )

    # Quantity and mode
    quantity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=1)
    # Risk-style mode active at entry: conservative|balanced|aggressive|scalper.
    # Feeds ModeAnalyticsEngine and the Trade Desk mode badge.
    trading_mode_at_entry: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Who/what approved the trade: manual|autopilot|user|copilot|scan_panel.
    # A separate concept from trading_mode_at_entry — do not conflate the two.
    approved_by: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # Market regime at entry (e.g. low_vol_trending) — same value already
    # threaded into JournalEntry.market_context; this makes it queryable
    # for a regime-bucketed performance ledger without a join.
    regime: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    # AI & execution
    signal_score: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 4), nullable=True)
    commission_paid: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    fill_price_short: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)
    fill_price_long: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 4), nullable=True)

    # Idempotent fill recording — broker dispatch correlation
    dispatch_id: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, unique=True, index=True,
        comment="Broker dispatch correlation ID — UNIQUE ensures idempotent fill recording"
    )
    strategy_snapshot_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True,
        comment="Configuration snapshot used when this trade was entered"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )
