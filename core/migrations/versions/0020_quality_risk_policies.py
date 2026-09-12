"""add governed quality risk policies

Revision ID: 0020_quality_policies
Revises: 0019_quality_risk
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020_quality_policies"
down_revision: str | None = "0019_quality_risk"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "quality_risk_policies",
        sa.Column("policy_id", sa.String(length=36), nullable=False),
        sa.Column("policy_key", sa.String(length=256), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("record_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("product_revision_id", sa.String(length=64), nullable=True),
        sa.Column("operation_code", sa.String(length=64), nullable=True),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("change_reason", sa.String(length=512), nullable=False),
        sa.Column("created_by", sa.String(length=64), nullable=False),
        sa.Column("submitted_by", sa.String(length=64), nullable=True),
        sa.Column("approved_by", sa.String(length=64), nullable=True),
        sa.Column("approval_reason", sa.String(length=512), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("policy_id"),
        sa.UniqueConstraint("policy_key", "version", name="uq_quality_risk_policy_version"),
    )
    op.create_index("ix_quality_risk_policies_policy_key", "quality_risk_policies", ["policy_key"])
    op.create_index("ix_quality_risk_policies_status", "quality_risk_policies", ["status"])
    op.create_index(
        "ix_quality_risk_policies_product_revision_id",
        "quality_risk_policies",
        ["product_revision_id"],
    )
    op.create_index(
        "ix_quality_risk_policies_operation_code", "quality_risk_policies", ["operation_code"]
    )
    op.create_index(
        "ix_quality_risk_policies_effective_from", "quality_risk_policies", ["effective_from"]
    )


def downgrade() -> None:
    op.drop_table("quality_risk_policies")
