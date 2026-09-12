"""persist deterministic quality risk assessments

Revision ID: 0019_quality_risk
Revises: 0018_unique_quality_target
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019_quality_risk"
down_revision: str | None = "0018_unique_quality_target"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_action_proposals", sa.Column("quality_risk_score", sa.Integer(), nullable=True)
    )
    op.add_column(
        "agent_action_proposals",
        sa.Column("quality_risk_level", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "agent_action_proposals",
        sa.Column("recommended_sample_size", sa.Integer(), nullable=True),
    )
    op.add_column(
        "agent_action_proposals",
        sa.Column("assessment_factors", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "agent_action_proposals",
        sa.Column("assessment_ruleset", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_action_proposals", "assessment_ruleset")
    op.drop_column("agent_action_proposals", "assessment_factors")
    op.drop_column("agent_action_proposals", "recommended_sample_size")
    op.drop_column("agent_action_proposals", "quality_risk_level")
    op.drop_column("agent_action_proposals", "quality_risk_score")
