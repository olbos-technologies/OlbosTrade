"""A saved read-only paper account pairing, never an execution permission."""
import uuid
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class PaperComparison(Base):
    __tablename__ = "paper_comparisons"
    organization_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    revision: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, default=uuid.uuid4)
    system_grant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("broker_oauth_grants.id", ondelete="CASCADE"), nullable=False)
    hybrid_grant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("broker_oauth_grants.id", ondelete="CASCADE"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    __table_args__ = (CheckConstraint("system_grant_id <> hybrid_grant_id", name="ck_paper_comparison_distinct"),)
