"""add bounded outbox timeline index

Revision ID: 0015_outbox_timeline_index
Revises: 0014_work_order_query_indexes
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0015_outbox_timeline_index"
down_revision: str | None = "0014_work_order_query_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_event_outbox_occurred_at_event_id",
        "event_outbox",
        ["occurred_at", "event_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_event_outbox_occurred_at_event_id", table_name="event_outbox")
