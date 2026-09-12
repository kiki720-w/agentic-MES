"""add process resource evidence to execution sessions

Revision ID: 0010_execution_process_resources
Revises: 0009_execution_material_trace
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_execution_process_resources"
down_revision: str | None = "0009_execution_material_trace"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "execution_sessions",
        sa.Column("resource_context", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )


def downgrade() -> None:
    op.drop_column("execution_sessions", "resource_context")
