"""add policy simulation evidence

Revision ID: 0021_policy_simulation
Revises: 0020_quality_policies
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021_policy_simulation"
down_revision: str | None = "0020_quality_policies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "quality_risk_policies", sa.Column("simulation_run_id", sa.String(36), nullable=True)
    )
    op.add_column(
        "quality_risk_policies", sa.Column("simulation_summary", sa.JSON(), nullable=True)
    )
    op.add_column(
        "quality_risk_policies", sa.Column("simulated_by", sa.String(64), nullable=True)
    )
    op.add_column(
        "quality_risk_policies",
        sa.Column("simulated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("quality_risk_policies", "simulated_at")
    op.drop_column("quality_risk_policies", "simulated_by")
    op.drop_column("quality_risk_policies", "simulation_summary")
    op.drop_column("quality_risk_policies", "simulation_run_id")
