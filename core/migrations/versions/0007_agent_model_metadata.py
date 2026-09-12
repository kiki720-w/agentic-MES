"""add agent model provenance

Revision ID: 0007_agent_model_metadata
Revises: 0006_quality_inspections
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_agent_model_metadata"
down_revision: str | None = "0006_quality_inspections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_action_proposals",
        sa.Column("narrative_source", sa.String(32), nullable=False, server_default="RULES"),
    )
    op.add_column(
        "agent_action_proposals", sa.Column("model_name", sa.String(96), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("agent_action_proposals", "model_name")
    op.drop_column("agent_action_proposals", "narrative_source")
