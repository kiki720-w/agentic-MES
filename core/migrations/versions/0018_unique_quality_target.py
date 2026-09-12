"""prevent duplicate inspections for one operation

Revision ID: 0018_unique_quality_target
Revises: 0017_operation_rows
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018_unique_quality_target"
down_revision: str | None = "0017_operation_rows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_quality_work_order_operation",
        "quality_inspections",
        ["work_order_id", "operation_sequence"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_quality_work_order_operation",
        table_name="quality_inspections",
    )
