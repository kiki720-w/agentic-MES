"""add frozen production operations to work orders

Revision ID: 0003_work_order_operations
Revises: 0002_outbox_leases
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_work_order_operations"
down_revision: str | None = "0002_outbox_leases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "work_orders",
        sa.Column("operations", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    op.drop_column("work_orders", "operations")
