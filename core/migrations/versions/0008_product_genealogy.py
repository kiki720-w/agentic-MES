"""add product serial genealogy

Revision ID: 0008_product_genealogy
Revises: 0007_agent_model_metadata
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_product_genealogy"
down_revision: str | None = "0007_agent_model_metadata"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "product_units",
        sa.Column("product_serial", sa.String(96), primary_key=True),
        sa.Column("work_order_id", sa.String(36), nullable=False),
        sa.Column("product_revision_id", sa.String(64), nullable=False),
        sa.Column("genealogy_status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_product_units_work_order_id", "product_units", ["work_order_id"])
    op.create_index(
        "ix_product_units_product_revision_id", "product_units", ["product_revision_id"]
    )
    op.create_table(
        "genealogy_links",
        sa.Column("link_id", sa.String(36), primary_key=True),
        sa.Column(
            "product_serial",
            sa.String(96),
            sa.ForeignKey("product_units.product_serial"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(32), nullable=False),
        sa.Column("object_type", sa.String(64), nullable=False),
        sa.Column("object_id", sa.String(128), nullable=False),
        sa.Column("operation_sequence", sa.Integer(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "product_serial",
            "relation_type",
            "object_type",
            "object_id",
            "operation_sequence",
            name="uq_genealogy_link_fact",
        ),
    )
    op.create_index("ix_genealogy_links_product_serial", "genealogy_links", ["product_serial"])
    op.create_index("ix_genealogy_links_object_id", "genealogy_links", ["object_id"])


def downgrade() -> None:
    op.drop_table("genealogy_links")
    op.drop_table("product_units")
