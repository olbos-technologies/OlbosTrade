"""SQLAlchemy model for zero_dte_observations: what a 0DTE debit spread looked like, and what
it was worth 5 and 10 minutes later. A RECORD, never an order.

One row per (ET trade date, slot, symbol, direction). The recorder (services/zero_dte_recorder.py)
asks the 0DTE proposal logic what it WOULD have proposed at fixed times of day, stores the real
leg quotes, then re-quotes the same two legs after 5 and 10 minutes. That gives a cost-inclusive
result (what the spread would have fetched if closed at the natural price) for every setup,
which free historical data cannot supply.

A refusal is a row too (`proposed` false, `refusal_reasons` set). How often the quotes are too
wide or stale to trade is itself a finding, and dropping those rows would flatter the record.

Nothing here is ever updated except `marks`, which gains one entry per horizon.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, Float, Index, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ZeroDteObservation(Base):
    __tablename__ = "zero_dte_observations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    trade_date: Mapped[date] = mapped_column(Date, nullable=False)          # ET calendar date
    slot: Mapped[str] = mapped_column(String(5), nullable=False)            # "10:30" (ET)
    symbol: Mapped[str] = mapped_column(String(10), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)       # bullish | bearish
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    spot: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    proposed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    refusal_reasons: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    # Filled only when proposed. Prices are per share, as quoted.
    option_type: Mapped[Optional[str]] = mapped_column(String(4), nullable=True)
    long_strike: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    short_strike: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    width: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    long_bid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    long_ask: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    short_bid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    short_ask: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    mid_debit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    natural_debit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # [{"minutes": 5, "at": iso, "elapsed_s": 312, "spot": ..., "quote_ok": bool,
    #   "long_bid": ..., "long_ask": ..., "short_bid": ..., "short_ask": ...,
    #   "natural_credit": long_bid - short_ask, "mid_credit": long_mid - short_mid}]
    # A mark whose quotes could not be read has quote_ok false and null prices: it is recorded
    # as unreadable, never filled in.
    marks: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # Free-form context for later splitting of results (e.g. the day's VIX), set at observation.
    context: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("trade_date", "slot", "symbol", "direction", name="uq_zero_dte_obs_slot"),
        Index("idx_zero_dte_obs_trade_date", "trade_date"),
    )
