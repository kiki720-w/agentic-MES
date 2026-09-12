"""add execution sessions and material trace

Revision ID: 0009_execution_material_trace
Revises: 0008_product_genealogy
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_execution_material_trace"
down_revision: str | None = "0008_product_genealogy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "execution_sessions",
        sa.Column("session_id", sa.String(96), primary_key=True),
        sa.Column(
            "product_serial",
            sa.String(96),
            sa.ForeignKey("product_units.product_serial"),
            nullable=False,
        ),
        sa.Column("work_order_id", sa.String(36), nullable=False),
        sa.Column("operation_sequence", sa.Integer(), nullable=False),
        sa.Column("operator_id", sa.String(64), nullable=False),
        sa.Column("equipment_id", sa.String(36), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("product_serial", "work_order_id", "operator_id", "equipment_id"):
        op.create_index(f"ix_execution_sessions_{column}", "execution_sessions", [column])
    op.create_table(
        "material_consumptions",
        sa.Column("consumption_id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(96),
            sa.ForeignKey("execution_sessions.session_id"),
            nullable=False,
        ),
        sa.Column("material_lot", sa.String(96), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("unit", sa.String(16), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_material_consumptions_session_id", "material_consumptions", ["session_id"])
    op.create_index(
        "ix_material_consumptions_material_lot", "material_consumptions", ["material_lot"]
    )


def downgrade() -> None:
    op.drop_table("material_consumptions")
    op.drop_table("execution_sessions")
