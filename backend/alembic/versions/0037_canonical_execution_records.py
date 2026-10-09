"""canonical trade intent, OMS order, fill ledger and outbox

MASTER_ARCHITECTURE §7.3-7.5 and §8.1. Ten tables that record what was asked
for, what risk decided, who approved it, what the OMS did about it, what the
broker actually executed, and which events still need publishing.

INERT. Nothing writes any of this yet, exactly as 0034 was inert when broker
connections landed. Routing the live order path through these records is §21
Phase 3; this migration puts the records and their CONSTRAINTS in place first,
so the guarantees can be argued about before anything depends on them. Creating
tables changes no behaviour, which is the point — the money path is untouched.

The constraints are the substance, not the columns:

  * trade_intents (organization_id, command_type, idempotency_key) UNIQUE
    §8.1's "the API requires an idempotency key for every mutating trading
    command", scoped per organization. A retried submit cannot become a
    second intent, so a double-tapped button cannot become two orders.

  * oms_orders (intent_id) UNIQUE
    carries that guarantee forward: one canonical order per intent, so a
    redelivered dispatch cannot create a second order.

  * oms_fills (broker, account_ref, broker_execution_id) UNIQUE
    §7.3's "unique per provider/account". Reconciliation and a websocket
    update both report the same execution; without this the fill is counted
    twice and the position silently doubles.

  * oms_order_attempts (client_order_id) UNIQUE
    after a timeout the only way to ask the broker "did you get this" is by
    the id that was sent, so it must identify exactly one dispatch.

  * oms_order_events (order_id, sequence) UNIQUE
    an append-only log that can be replayed in order even when two rows share
    a timestamp.

  * inbox_receipts PRIMARY KEY (consumer, event_id)
    the collision on a redelivered event IS the deduplication.

Numeric, never float, for every quantity and price: a contract count that
rounds is a position that does not reconcile.

Revision ID: 0037
Revises: 0036
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inbox_receipts",
        sa.Column("consumer", sa.String(60), nullable=False, primary_key=True),
        sa.Column("event_id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_inbox_receipts_processed", "inbox_receipts", ["processed_at"])
    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("aggregate_type", sa.String(40), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_outbox_aggregate", "outbox_events", ["aggregate_type", "aggregate_id"])
    op.create_index("idx_outbox_pending", "outbox_events", ["created_at"], postgresql_where=sa.text("status = 'pending'"))
    op.create_table(
        "trade_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("service_identity", sa.String(60), nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("execution_mode", sa.String(20), nullable=False),
        sa.Column("broker_connection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("broker_connections.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("broker_connection_version", sa.Integer(), nullable=True),
        sa.Column("environment", sa.String(10), nullable=False),
        sa.Column("account_ref", sa.String(64), nullable=True),
        sa.Column("asset_class", sa.String(20), nullable=False),
        sa.Column("canonical_instrument_id", sa.String(64), nullable=True),
        sa.Column("symbol_display", sa.String(32), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=True),
        sa.Column("sizing_request", postgresql.JSONB(), nullable=True),
        sa.Column("order_type", sa.String(20), nullable=False),
        sa.Column("limit_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("stop_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("time_in_force", sa.String(12), nullable=False, server_default="day"),
        sa.Column("option_legs", postgresql.JSONB(), nullable=True),
        sa.Column("strategy_id", sa.String(64), nullable=True),
        sa.Column("strategy_version", sa.String(32), nullable=True),
        sa.Column("signal_snapshot_id", sa.String(64), nullable=True),
        sa.Column("entry_reason", sa.Text(), nullable=True),
        sa.Column("invalidation", sa.Text(), nullable=True),
        sa.Column("target", sa.Text(), nullable=True),
        sa.Column("maximum_risk_request", sa.Numeric(20, 8), nullable=True),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trade_intents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("command_type", sa.String(32), nullable=False, server_default="create_order"),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_trade_intents_correlation", "trade_intents", ["correlation_id"])
    op.create_index("idx_trade_intents_idempotency", "trade_intents", ["organization_id", "command_type", "idempotency_key"], unique=True)
    op.create_index("idx_trade_intents_org_created", "trade_intents", ["organization_id", "created_at"])
    op.create_table(
        "intent_evaluations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("intent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trade_intents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("result", sa.String(20), nullable=False),
        sa.Column("block_reasons", postgresql.JSONB(), nullable=True),
        sa.Column("approved_quantity", sa.Numeric(20, 8), nullable=True),
        sa.Column("policy_version", sa.String(32), nullable=True),
        sa.Column("policy_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("data_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("data_as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_intent_evaluations_intent", "intent_evaluations", ["intent_id"])
    op.create_table(
        "oms_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("intent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trade_intents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("broker_connection_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("broker_connections.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("broker_connection_version", sa.Integer(), nullable=True),
        sa.Column("environment", sa.String(10), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="CREATED"),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column("symbol_display", sa.String(32), nullable=False),
        sa.Column("asset_class", sa.String(20), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("order_type", sa.String(20), nullable=False),
        sa.Column("time_in_force", sa.String(12), nullable=False, server_default="day"),
        sa.Column("limit_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("stop_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("filled_quantity", sa.Numeric(20, 8), nullable=False, server_default=sa.text('0')),
        sa.Column("average_fill_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("parent_order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("oms_orders.id", ondelete="SET NULL"), nullable=True),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_oms_orders_correlation", "oms_orders", ["correlation_id"])
    op.create_index("idx_oms_orders_intent", "oms_orders", ["intent_id"], unique=True)
    op.create_index("idx_oms_orders_org_state", "oms_orders", ["organization_id", "state"])
    op.create_table(
        "approval_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("intent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("trade_intents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evaluation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("intent_evaluations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("decided_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decided_by_policy", sa.String(64), nullable=True),
        sa.Column("step_up_evidence", postgresql.JSONB(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_approval_decisions_intent", "approval_decisions", ["intent_id"])
    op.create_table(
        "oms_order_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("oms_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("client_order_id", sa.String(128), nullable=False),
        sa.Column("broker_order_id", sa.String(128), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_oms_attempts_client_order_id", "oms_order_attempts", ["client_order_id"], unique=True)
    op.create_index("idx_oms_attempts_order_number", "oms_order_attempts", ["order_id", "attempt_number"], unique=True)
    op.create_table(
        "oms_order_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("oms_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.String(24), nullable=True),
        sa.Column("to_state", sa.String(24), nullable=False),
        sa.Column("actor", sa.String(64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_oms_events_order_sequence", "oms_order_events", ["order_id", "sequence"], unique=True)
    op.create_table(
        "oms_order_legs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("oms_orders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("leg_index", sa.Integer(), nullable=False),
        sa.Column("symbol_display", sa.String(32), nullable=False),
        sa.Column("asset_class", sa.String(20), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("expiry", sa.DateTime(timezone=True), nullable=True),
        sa.Column("strike", sa.Numeric(20, 8), nullable=True),
        sa.Column("right", sa.String(4), nullable=True),
    )
    op.create_index("idx_oms_order_legs_order_index", "oms_order_legs", ["order_id", "leg_index"], unique=True)
    op.create_table(
        "oms_fills",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False, primary_key=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("oms_orders.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("leg_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("oms_order_legs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("broker_execution_id", sa.String(128), nullable=False),
        sa.Column("broker", sa.String(20), nullable=False),
        sa.Column("account_ref", sa.String(64), nullable=False, server_default=""),
        sa.Column("quantity", sa.Numeric(20, 8), nullable=False),
        sa.Column("price", sa.Numeric(20, 8), nullable=False),
        sa.Column("fee", sa.Numeric(20, 8), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_oms_fills_order", "oms_fills", ["order_id"])
    op.create_index("idx_oms_fills_unique_execution", "oms_fills", ["broker", "account_ref", "broker_execution_id"], unique=True)


def downgrade() -> None:
    # Reverse creation order: children before the tables they reference.
    for table in (
        "oms_fills",
        "oms_order_events",
        "oms_order_attempts",
        "oms_order_legs",
        "oms_orders",
        "approval_decisions",
        "intent_evaluations",
        "trade_intents",
        "outbox_events",
        "inbox_receipts",
    ):
        op.drop_table(table)
