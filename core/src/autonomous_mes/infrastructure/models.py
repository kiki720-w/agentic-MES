from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class WorkOrderRow(Base):
    __tablename__ = "work_orders"
    __table_args__ = (UniqueConstraint("human_code", name="uq_work_orders_human_code"),)

    work_order_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    human_code: Mapped[str] = mapped_column(String(64), nullable=False)
    production_order_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workshop_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    product_revision_id: Mapped[str] = mapped_column(String(64), nullable=False)
    routing_revision_id: Mapped[str] = mapped_column(String(64), nullable=False)
    bom_revision_id: Mapped[str] = mapped_column(String(64), nullable=False)
    drawing_revision_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    operations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IdempotencyRecordRow(Base):
    __tablename__ = "idempotency_records"

    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(36), nullable=False)
    resource_version: Mapped[int] = mapped_column(Integer, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EventOutboxRow(Base):
    __tablename__ = "event_outbox"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    causation_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    publish_status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)


class AgentToolAuditRow(Base):
    __tablename__ = "agent_tool_audits"

    audit_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str] = mapped_column(String(256), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(96), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(16), nullable=False)
    risk: Mapped[str] = mapped_column(String(8), nullable=False)
    policy_decision: Mapped[str] = mapped_column(String(16), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(64), nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EquipmentRow(Base):
    __tablename__ = "equipment"

    equipment_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    workshop_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    work_center_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    protocol: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    spindle_load_percent: Mapped[float | None] = mapped_column(Float)
    temperature_celsius: Mapped[float | None] = mapped_column(Float)
    alarm_code: Mapped[str | None] = mapped_column(String(64))
    downtime_reason: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EquipmentTelemetryRow(Base):
    __tablename__ = "equipment_telemetry"

    sample_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    equipment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("equipment.equipment_id"), nullable=False, index=True
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    spindle_load_percent: Mapped[float | None] = mapped_column(Float)
    temperature_celsius: Mapped[float | None] = mapped_column(Float)
    alarm_code: Mapped[str | None] = mapped_column(String(64))
    downtime_reason: Mapped[str | None] = mapped_column(String(256))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentProposalRow(Base):
    __tablename__ = "agent_action_proposals"

    proposal_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    risk: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    work_order_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    work_order_version: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    equipment_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    equipment_version: Mapped[int] = mapped_column(Integer, nullable=False)
    diagnosis: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    narrative_source: Mapped[str] = mapped_column(String(32), nullable=False, default="RULES")
    model_name: Mapped[str | None] = mapped_column(String(96))
    approved_by: Mapped[str | None] = mapped_column(String(64))
    approval_reason: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QualityInspectionRow(Base):
    __tablename__ = "quality_inspections"

    inspection_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    work_order_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    operation_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    result: Mapped[str | None] = mapped_column(String(16))
    defect_code: Mapped[str | None] = mapped_column(String(64))
    notes: Mapped[str | None] = mapped_column(String(512))
    rework_route: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    gauge_id: Mapped[str | None] = mapped_column(String(64), index=True)
    calibration_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    measurement_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ProductUnitRow(Base):
    __tablename__ = "product_units"

    product_serial: Mapped[str] = mapped_column(String(96), primary_key=True)
    work_order_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    product_revision_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    genealogy_status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GenealogyLinkRow(Base):
    __tablename__ = "genealogy_links"
    __table_args__ = (
        UniqueConstraint(
            "product_serial",
            "relation_type",
            "object_type",
            "object_id",
            "operation_sequence",
            name="uq_genealogy_link_fact",
        ),
    )

    link_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    product_serial: Mapped[str] = mapped_column(
        String(96), ForeignKey("product_units.product_serial"), nullable=False, index=True
    )
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    operation_sequence: Mapped[int | None] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExecutionSessionRow(Base):
    __tablename__ = "execution_sessions"

    session_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    product_serial: Mapped[str] = mapped_column(
        String(96), ForeignKey("product_units.product_serial"), nullable=False, index=True
    )
    work_order_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    operation_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    operator_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    equipment_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resource_context: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)


class MaterialConsumptionRow(Base):
    __tablename__ = "material_consumptions"

    consumption_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        String(96), ForeignKey("execution_sessions.session_id"), nullable=False, index=True
    )
    material_lot: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ManufacturingResourceRow(Base):
    __tablename__ = "manufacturing_resources"
    __table_args__ = (
        UniqueConstraint(
            "resource_type", "resource_id", "revision", name="uq_manufacturing_resource_key"
        ),
    )

    resource_key: Mapped[str] = mapped_column(String(256), primary_key=True)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    resource_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    revision: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    life_remaining_percent: Mapped[float | None] = mapped_column(Float)
    calibration_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    external_reference: Mapped[str | None] = mapped_column(String(256))
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
