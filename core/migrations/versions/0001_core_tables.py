"""create work order, idempotency, outbox and agent audit tables"""

import sqlalchemy as sa
from alembic import op

revision = "0001_core_tables"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "work_orders",
        sa.Column("work_order_id", sa.String(36), primary_key=True),
        sa.Column("human_code", sa.String(64), nullable=False),
        sa.Column("production_order_id", sa.String(64), nullable=False),
        sa.Column("workshop_id", sa.String(64), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("product_revision_id", sa.String(64), nullable=False),
        sa.Column("routing_revision_id", sa.String(64), nullable=False),
        sa.Column("bom_revision_id", sa.String(64), nullable=False),
        sa.Column("drawing_revision_ids", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_work_orders_quantity_positive"),
        sa.CheckConstraint("priority BETWEEN 1 AND 100", name="ck_work_orders_priority"),
        sa.UniqueConstraint("human_code", name="uq_work_orders_human_code"),
    )
    op.create_index("ix_work_orders_production_order_id", "work_orders", ["production_order_id"])
    op.create_index("ix_work_orders_workshop_id", "work_orders", ["workshop_id"])

    op.create_table(
        "idempotency_records",
        sa.Column("idempotency_key", sa.String(128), primary_key=True),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(36), nullable=False),
        sa.Column("resource_version", sa.Integer(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "event_outbox",
        sa.Column("event_id", sa.String(36), primary_key=True),
        sa.Column("event_type", sa.String(96), nullable=False),
        sa.Column("schema_version", sa.String(16), nullable=False),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("correlation_id", sa.String(64)),
        sa.Column("causation_id", sa.String(64)),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("publish_status", sa.String(16), server_default="PENDING", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.CheckConstraint("attempts >= 0", name="ck_event_outbox_attempts"),
    )
    op.create_index("ix_event_outbox_event_type", "event_outbox", ["event_type"])
    op.create_index("ix_event_outbox_aggregate_id", "event_outbox", ["aggregate_id"])
    op.create_index(
        "ix_event_outbox_pending",
        "event_outbox",
        ["publish_status", "next_attempt_at", "occurred_at"],
    )

    op.create_table(
        "agent_tool_audits",
        sa.Column("audit_id", sa.String(36), primary_key=True),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("agent_id", sa.String(64), nullable=False),
        sa.Column("subject_id", sa.String(64), nullable=False),
        sa.Column("purpose", sa.String(256), nullable=False),
        sa.Column("tool_name", sa.String(96), nullable=False),
        sa.Column("tool_version", sa.String(16), nullable=False),
        sa.Column("risk", sa.String(8), nullable=False),
        sa.Column("policy_decision", sa.String(16), nullable=False),
        sa.Column("object_type", sa.String(64), nullable=False),
        sa.Column("object_id", sa.String(64), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_agent_tool_audits_request_id", "agent_tool_audits", ["request_id"])


def downgrade() -> None:
    op.drop_table("agent_tool_audits")
    op.drop_table("event_outbox")
    op.drop_table("idempotency_records")
    op.drop_table("work_orders")
