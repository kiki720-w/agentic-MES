"""add connector anti replay receipts

Revision ID: 0013_connector_receipts
Revises: 0012_resource_master
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_connector_receipts"
down_revision: str | None = "0012_resource_master"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "connector_receipts",
        sa.Column("nonce", sa.String(128), primary_key=True),
        sa.Column("key_id", sa.String(96), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_connector_receipts_key_id", "connector_receipts", ["key_id"])
    op.create_index("ix_connector_receipts_received_at", "connector_receipts", ["received_at"])


def downgrade() -> None:
    op.drop_table("connector_receipts")
