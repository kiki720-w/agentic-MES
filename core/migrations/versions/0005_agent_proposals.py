"""add governed agent action proposals

Revision ID: 0005_agent_proposals
Revises: 0004_equipment_telemetry
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_agent_proposals"
down_revision: str | None = "0004_equipment_telemetry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_action_proposals",
        sa.Column("proposal_id", sa.String(36), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("agent_id", sa.String(64), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("risk", sa.String(8), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("work_order_id", sa.String(36), nullable=False),
        sa.Column("work_order_version", sa.Integer(), nullable=False),
        sa.Column("operation_sequence", sa.Integer(), nullable=False),
        sa.Column("equipment_id", sa.String(36), nullable=False),
        sa.Column("equipment_version", sa.Integer(), nullable=False),
        sa.Column("diagnosis", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("approved_by", sa.String(64)),
        sa.Column("approval_reason", sa.String(512)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_action_proposals_status", "agent_action_proposals", ["status"])
    op.create_index(
        "ix_agent_action_proposals_work_order_id",
        "agent_action_proposals",
        ["work_order_id"],
    )
    op.create_index(
        "ix_agent_action_proposals_equipment_id",
        "agent_action_proposals",
        ["equipment_id"],
    )


def downgrade() -> None:
    op.drop_table("agent_action_proposals")
