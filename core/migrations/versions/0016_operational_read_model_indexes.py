"""add operational read model indexes

Revision ID: 0016_operational_indexes
Revises: 0015_outbox_timeline_index
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016_operational_indexes"
down_revision: str | None = "0015_outbox_timeline_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_equipment_state_updated_id",
        "equipment",
        ["state", "updated_at", "equipment_id"],
    )
    op.create_index(
        "ix_quality_status_updated_id",
        "quality_inspections",
        ["status", "updated_at", "inspection_id"],
    )
    op.create_index(
        "ix_resource_type_updated_key",
        "manufacturing_resources",
        ["resource_type", "updated_at", "resource_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_resource_type_updated_key",
        table_name="manufacturing_resources",
    )
    op.drop_index(
        "ix_quality_status_updated_id",
        table_name="quality_inspections",
    )
    op.drop_index("ix_equipment_state_updated_id", table_name="equipment")
