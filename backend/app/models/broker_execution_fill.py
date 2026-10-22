"""R2-E durable, account-scoped broker execution ledger (schema only).

Only authenticated broker execution ingestion may populate this table in a
follow-up PR. Never synthesize executions from pending orders or trade intent.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class BrokerExecutionFill(Base):
    __tablename__ = "broker_execution_fills"
    __table_args__ = (
        UniqueConstraint("broker", "broker_account_id", "broker_execution_id",
                         name="uq_broker_execution_fills_identity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    broker: Mapped[str] = mapped_column(String(32), nullable=False)
    broker_account_id: Mapped[str] = mapped_column(String(128), nullable=False)
    broker_execution_id: Mapped[str] = mapped_column(String(160), nullable=False)
    organization_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    client_order_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    contract_id: Mapped[str] = mapped_column(String(160), nullable=False)
    underlying: Mapped[str] = mapped_column(String(24), nullable=False)
    expiration: Mapped[str] = mapped_column(String(10), nullable=False)
    strike: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    option_type: Mapped[str] = mapped_column(String(8), nullable=False)
    signed_filled_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    fill_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
