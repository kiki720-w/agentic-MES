"""add outbox worker lease fields"""

import sqlalchemy as sa
from alembic import op

revision = "0002_outbox_leases"
down_revision = "0001_core_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("event_outbox", sa.Column("locked_at", sa.DateTime(timezone=True)))
    op.add_column("event_outbox", sa.Column("locked_by", sa.String(64)))
    op.create_index("ix_event_outbox_locked_at", "event_outbox", ["locked_at"])


def downgrade() -> None:
    op.drop_index("ix_event_outbox_locked_at", table_name="event_outbox")
    op.drop_column("event_outbox", "locked_by")
    op.drop_column("event_outbox", "locked_at")
