"""add work order operations-console query indexes

Revision ID: 0014_work_order_query_indexes
Revises: 0013_connector_receipts
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0014_work_order_query_indexes"
down_revision: str | None = "0013_connector_receipts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_work_orders_status_updated_at",
        "work_orders",
        ["status", "updated_at"],
    )
    op.create_index("ix_work_orders_updated_at", "work_orders", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_work_orders_updated_at", table_name="work_orders")
    op.drop_index("ix_work_orders_status_updated_at", table_name="work_orders")
