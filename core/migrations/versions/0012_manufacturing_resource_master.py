"""add manufacturing resource master

Revision ID: 0012_resource_master
Revises: 0011_quality_gauge_trace
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_resource_master"
down_revision: str | None = "0011_quality_gauge_trace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "manufacturing_resources",
        sa.Column("resource_key", sa.String(256), primary_key=True),
        sa.Column("resource_type", sa.String(32), nullable=False),
        sa.Column("resource_id", sa.String(96), nullable=False),
        sa.Column("revision", sa.String(64), nullable=False, server_default=""),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("life_remaining_percent", sa.Float()),
        sa.Column("calibration_due_at", sa.DateTime(timezone=True)),
        sa.Column("source_system", sa.String(64), nullable=False),
        sa.Column("external_reference", sa.String(256)),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "resource_type", "resource_id", "revision", name="uq_manufacturing_resource_key"
        ),
    )
    for column in ("resource_type", "resource_id", "status"):
        op.create_index(
            f"ix_manufacturing_resources_{column}", "manufacturing_resources", [column]
        )


def downgrade() -> None:
    op.drop_table("manufacturing_resources")
