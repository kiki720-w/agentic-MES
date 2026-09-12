"""add versioned scheduling snapshots

Revision ID: 0023_scheduling_snapshots
Revises: 0022_unified_aps
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_scheduling_snapshots"
down_revision: str | None = "0022_unified_aps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "scheduling_snapshots",
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("workshop_id", sa.String(length=64), nullable=False),
        sa.Column("source_revision", sa.String(length=128), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("snapshot_id"),
        sa.UniqueConstraint(
            "source_system",
            "workshop_id",
            "source_revision",
            name="uq_scheduling_snapshot_source_revision",
        ),
    )
    op.create_index(
        "ix_scheduling_snapshots_source_system",
        "scheduling_snapshots",
        ["source_system"],
    )
    op.create_index(
        "ix_scheduling_snapshots_workshop_id",
        "scheduling_snapshots",
        ["workshop_id"],
    )
    op.create_index(
        "ix_scheduling_snapshots_observed_at",
        "scheduling_snapshots",
        ["observed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_scheduling_snapshots_observed_at", table_name="scheduling_snapshots")
    op.drop_index("ix_scheduling_snapshots_workshop_id", table_name="scheduling_snapshots")
    op.drop_index("ix_scheduling_snapshots_source_system", table_name="scheduling_snapshots")
    op.drop_table("scheduling_snapshots")
