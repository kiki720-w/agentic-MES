"""add gauge calibration evidence to quality inspections

Revision ID: 0011_quality_gauge_trace
Revises: 0010_execution_process_resources
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_quality_gauge_trace"
down_revision: str | None = "0010_execution_process_resources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("quality_inspections", sa.Column("gauge_id", sa.String(64)))
    op.add_column(
        "quality_inspections", sa.Column("calibration_due_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "quality_inspections", sa.Column("measurement_recorded_at", sa.DateTime(timezone=True))
    )
    op.create_index("ix_quality_inspections_gauge_id", "quality_inspections", ["gauge_id"])


def downgrade() -> None:
    op.drop_index("ix_quality_inspections_gauge_id", table_name="quality_inspections")
    op.drop_column("quality_inspections", "measurement_recorded_at")
    op.drop_column("quality_inspections", "calibration_due_at")
    op.drop_column("quality_inspections", "gauge_id")
