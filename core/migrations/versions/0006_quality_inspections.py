"""add quality inspections and rework disposition

Revision ID: 0006_quality_inspections
Revises: 0005_agent_proposals
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_quality_inspections"
down_revision: str | None = "0005_agent_proposals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quality_inspections",
        sa.Column("inspection_id", sa.String(36), primary_key=True),
        sa.Column("work_order_id", sa.String(36), nullable=False),
        sa.Column("operation_sequence", sa.Integer(), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("result", sa.String(16)),
        sa.Column("defect_code", sa.String(64)),
        sa.Column("notes", sa.String(512)),
        sa.Column("rework_route", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_quality_inspections_work_order_id", "quality_inspections", ["work_order_id"]
    )
    op.create_index("ix_quality_inspections_status", "quality_inspections", ["status"])


def downgrade() -> None:
    op.drop_table("quality_inspections")
