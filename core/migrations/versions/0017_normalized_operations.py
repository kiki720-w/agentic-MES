"""normalize work order operations for query workloads

Revision ID: 0017_operation_rows
Revises: 0016_operational_indexes
"""

import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0017_operation_rows"
down_revision: str | None = "0016_operational_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_order_operations",
        sa.Column(
            "work_order_id",
            sa.String(length=36),
            sa.ForeignKey("work_orders.work_order_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column("operation_code", sa.String(length=64), nullable=False),
        sa.Column("operation_name", sa.String(length=160), nullable=False),
        sa.Column("work_center_id", sa.String(length=64), nullable=False),
        sa.Column("planned_quantity", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("assigned_resource_id", sa.String(length=36), nullable=True),
        sa.Column("good_quantity", sa.Integer(), nullable=False),
        sa.Column("scrap_quantity", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_operation_status_order_sequence",
        "work_order_operations",
        ["status", "work_order_id", "sequence"],
    )
    op.create_index(
        "ix_work_order_operations_work_center_id",
        "work_order_operations",
        ["work_center_id"],
    )
    op.create_index(
        "ix_work_order_operations_assigned_resource_id",
        "work_order_operations",
        ["assigned_resource_id"],
    )
    _backfill_operations()


def _backfill_operations() -> None:
    source = sa.table(
        "work_orders",
        sa.column("work_order_id", sa.String()),
        sa.column("operations", sa.JSON()),
    )
    target = sa.table(
        "work_order_operations",
        sa.column("work_order_id", sa.String()),
        sa.column("sequence", sa.Integer()),
        sa.column("operation_code", sa.String()),
        sa.column("operation_name", sa.String()),
        sa.column("work_center_id", sa.String()),
        sa.column("planned_quantity", sa.Integer()),
        sa.column("status", sa.String()),
        sa.column("assigned_resource_id", sa.String()),
        sa.column("good_quantity", sa.Integer()),
        sa.column("scrap_quantity", sa.Integer()),
    )
    rows: list[dict[str, Any]] = []
    connection = op.get_bind()
    for work_order_id, raw_operations in connection.execute(
        sa.select(source.c.work_order_id, source.c.operations)
    ):
        operations = json.loads(raw_operations) if isinstance(raw_operations, str) else raw_operations
        for item in operations or []:
            rows.append(
                {
                    "work_order_id": work_order_id,
                    "sequence": int(item["sequence"]),
                    "operation_code": str(item["operationCode"]),
                    "operation_name": str(item["operationName"]),
                    "work_center_id": str(item["workCenterId"]),
                    "planned_quantity": int(item["plannedQuantity"]),
                    "status": str(item.get("status", "PENDING")),
                    "assigned_resource_id": item.get("assignedResourceId"),
                    "good_quantity": int(item.get("goodQuantity", 0)),
                    "scrap_quantity": int(item.get("scrapQuantity", 0)),
                }
            )
            if len(rows) >= 1_000:
                op.bulk_insert(target, rows)
                rows.clear()
    if rows:
        op.bulk_insert(target, rows)


def downgrade() -> None:
    op.drop_table("work_order_operations")
