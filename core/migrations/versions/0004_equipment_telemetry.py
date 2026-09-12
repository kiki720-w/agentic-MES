"""add equipment registry and telemetry history

Revision ID: 0004_equipment_telemetry
Revises: 0003_work_order_operations
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_equipment_telemetry"
down_revision: str | None = "0003_work_order_operations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "equipment",
        sa.Column("equipment_id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("workshop_id", sa.String(64), nullable=False),
        sa.Column("work_center_id", sa.String(64), nullable=False),
        sa.Column("protocol", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("spindle_load_percent", sa.Float()),
        sa.Column("temperature_celsius", sa.Float()),
        sa.Column("alarm_code", sa.String(64)),
        sa.Column("downtime_reason", sa.String(256)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_equipment_workshop_id", "equipment", ["workshop_id"])
    op.create_index("ix_equipment_work_center_id", "equipment", ["work_center_id"])
    op.create_table(
        "equipment_telemetry",
        sa.Column("sample_id", sa.String(64), primary_key=True),
        sa.Column(
            "equipment_id",
            sa.String(36),
            sa.ForeignKey("equipment.equipment_id"),
            nullable=False,
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("spindle_load_percent", sa.Float()),
        sa.Column("temperature_celsius", sa.Float()),
        sa.Column("alarm_code", sa.String(64)),
        sa.Column("downtime_reason", sa.String(256)),
        sa.Column(
            "received_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_equipment_telemetry_equipment_id", "equipment_telemetry", ["equipment_id"])
    op.create_index(
        "ix_equipment_telemetry_observed",
        "equipment_telemetry",
        ["equipment_id", "observed_at"],
    )


def downgrade() -> None:
    op.drop_table("equipment_telemetry")
    op.drop_table("equipment")
