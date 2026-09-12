"""add unified finite-capacity APS planning

Revision ID: 0022_unified_aps
Revises: 0021_policy_simulation
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_unified_aps"
down_revision: str | None = "0021_policy_simulation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planning_resources",
        sa.Column("resource_id", sa.String(length=36), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("resource_type", sa.String(length=16), nullable=False),
        sa.Column("workshop_id", sa.String(length=64), nullable=False),
        sa.Column("work_center_id", sa.String(length=64), nullable=False),
        sa.Column("daily_capacity_minutes", sa.Float(), nullable=False),
        sa.Column("overtime_capacity_minutes", sa.Float(), nullable=False),
        sa.Column("capability_codes", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "workshop_id", "code", name="uq_planning_resource_workshop_code"
        ),
    )
    op.create_index(
        "ix_planning_resources_workshop_id", "planning_resources", ["workshop_id"]
    )
    op.create_index(
        "ix_planning_resources_work_center_id", "planning_resources", ["work_center_id"]
    )
    op.create_table(
        "schedule_plans",
        sa.Column("plan_id", sa.String(length=36), primary_key=True),
        sa.Column("plan_number", sa.String(length=64), nullable=False, unique=True),
        sa.Column("workshop_id", sa.String(length=64), nullable=False),
        sa.Column("horizon_start", sa.Date(), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("use_overtime", sa.Boolean(), nullable=False),
        sa.Column("generation_parameters", sa.JSON(), nullable=False),
        sa.Column("assignments", sa.JSON(), nullable=False),
        sa.Column("shortages", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("record_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("submitted_by", sa.String(length=64)),
        sa.Column("approved_by", sa.String(length=64)),
        sa.Column("approval_reason", sa.String(length=512)),
        sa.Column("published_by", sa.String(length=64)),
        sa.Column("withdrawn_by", sa.String(length=64)),
        sa.Column("withdrawal_reason", sa.String(length=512)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_schedule_plans_workshop_id", "schedule_plans", ["workshop_id"])
    op.create_index("ix_schedule_plans_status", "schedule_plans", ["status"])


def downgrade() -> None:
    op.drop_table("schedule_plans")
    op.drop_table("planning_resources")
