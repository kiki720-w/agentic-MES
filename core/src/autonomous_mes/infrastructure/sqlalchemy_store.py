from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from autonomous_mes.application.connector_security import ConnectorReceipt
from autonomous_mes.application.ports import IdempotentResult
from autonomous_mes.domain.agent import AgentProposal, ProposalStatus
from autonomous_mes.domain.equipment import Equipment, EquipmentState, TelemetrySample
from autonomous_mes.domain.errors import IdempotencyConflict, InvalidTransition
from autonomous_mes.domain.events import DomainEvent
from autonomous_mes.domain.genealogy import (
    ExecutionSession,
    GenealogyLink,
    MaterialConsumption,
    ProductUnit,
)
from autonomous_mes.domain.master_data import ManufacturingResource
from autonomous_mes.domain.quality import InspectionStatus, QualityInspection
from autonomous_mes.domain.work_order import (
    FrozenRevisions,
    OperationStatus,
    ProductionOperation,
    WorkOrder,
    WorkOrderStatus,
)

from .models import (
    AgentProposalRow,
    AgentToolAuditRow,
    ConnectorReceiptRow,
    EquipmentRow,
    EquipmentTelemetryRow,
    EventOutboxRow,
    ExecutionSessionRow,
    GenealogyLinkRow,
    IdempotencyRecordRow,
    ManufacturingResourceRow,
    MaterialConsumptionRow,
    ProductUnitRow,
    QualityInspectionRow,
    WorkOrderRow,
)


class SqlAlchemyWorkOrderStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def get(self, work_order_id: str) -> WorkOrder | None:
        with self._sessions() as session:
            row = session.get(WorkOrderRow, work_order_id)
            return _to_domain(row) if row else None

    def get_by_human_code(self, human_code: str) -> WorkOrder | None:
        with self._sessions() as session:
            row = session.scalar(select(WorkOrderRow).where(WorkOrderRow.human_code == human_code))
            return _to_domain(row) if row else None

    def list_work_orders(self, limit: int = 100) -> list[WorkOrder]:
        with self._sessions() as session:
            rows = session.scalars(
                select(WorkOrderRow).order_by(WorkOrderRow.updated_at.desc()).limit(limit)
            ).all()
            return [_to_domain(row) for row in rows]

    def get_idempotent_result(self, idempotency_key: str) -> IdempotentResult | None:
        with self._sessions() as session:
            row = session.get(IdempotencyRecordRow, idempotency_key)
            if row is None:
                return None
            return IdempotentResult(
                row.operation, row.resource_id, row.resource_version, row.request_hash
            )

    def save_atomically(
        self,
        work_order: WorkOrder,
        expected_stored_version: int | None,
        events: list[DomainEvent],
        idempotency_key: str,
        idempotent_result: IdempotentResult,
    ) -> IdempotentResult:
        try:
            with self._sessions.begin() as session:
                if expected_stored_version is None:
                    session.add(_to_row(work_order))
                else:
                    result = session.execute(
                        update(WorkOrderRow)
                        .where(
                            WorkOrderRow.work_order_id == work_order.work_order_id,
                            WorkOrderRow.version == expected_stored_version,
                        )
                        .values(**_row_values(work_order))
                    )
                    if getattr(result, "rowcount", 0) != 1:
                        raise InvalidTransition("optimistic lock conflict")

                session.add_all(_event_rows(events))
                session.add(
                    IdempotencyRecordRow(
                        idempotency_key=idempotency_key,
                        operation=idempotent_result.operation,
                        resource_id=idempotent_result.resource_id,
                        resource_version=idempotent_result.resource_version,
                        request_hash=idempotent_result.request_hash,
                    )
                )
            return idempotent_result
        except IntegrityError as exc:
            raise IdempotencyConflict("unique or idempotency constraint conflict") from exc

    def list_outbox(self) -> list[dict[str, Any]]:
        with self._sessions() as session:
            rows = session.scalars(
                select(EventOutboxRow).order_by(EventOutboxRow.occurred_at)
            ).all()
            return [
                {
                    "eventId": row.event_id,
                    "eventType": row.event_type,
                    "aggregateId": row.aggregate_id,
                    "occurredAt": row.occurred_at.isoformat(),
                    "payload": row.payload,
                    "publishStatus": row.publish_status,
                }
                for row in rows
            ]

    def record_tool_event(self, event: DomainEvent) -> None:
        payload = event.payload
        with self._sessions.begin() as session:
            session.add_all(_event_rows([event]))
            session.add(
                AgentToolAuditRow(
                    audit_id=str(uuid4()),
                    request_id=event.aggregate_id,
                    agent_id=payload["agentId"],
                    subject_id=payload["subjectId"],
                    purpose=payload["purpose"],
                    tool_name=payload["tool"],
                    tool_version=payload["toolVersion"],
                    risk=payload["risk"],
                    policy_decision=payload["policyDecision"],
                    object_type=payload["objectType"],
                    object_id=payload["objectId"],
                    detail={},
                )
            )

    def get_equipment(self, equipment_id: str) -> Equipment | None:
        with self._sessions() as session:
            row = session.get(EquipmentRow, equipment_id)
            return _equipment_to_domain(row) if row else None

    def get_equipment_by_code(self, code: str) -> Equipment | None:
        with self._sessions() as session:
            row = session.scalar(select(EquipmentRow).where(EquipmentRow.code == code))
            return _equipment_to_domain(row) if row else None

    def list_equipment(self, limit: int = 100) -> list[Equipment]:
        with self._sessions() as session:
            rows = session.scalars(
                select(EquipmentRow).order_by(EquipmentRow.updated_at.desc()).limit(limit)
            ).all()
            return [_equipment_to_domain(row) for row in rows]

    def telemetry_sample_exists(self, sample_id: str) -> bool:
        with self._sessions() as session:
            return session.get(EquipmentTelemetryRow, sample_id) is not None

    def add_equipment_atomically(self, equipment: Equipment, event: DomainEvent) -> None:
        try:
            with self._sessions.begin() as session:
                session.add(EquipmentRow(**_equipment_values(equipment)))
                session.add_all(_event_rows([event]))
        except IntegrityError as exc:
            raise IdempotencyConflict("equipment code already exists") from exc

    def record_telemetry_atomically(
        self,
        equipment: Equipment,
        expected_stored_version: int,
        sample: TelemetrySample,
        event: DomainEvent,
    ) -> None:
        try:
            with self._sessions.begin() as session:
                result = session.execute(
                    update(EquipmentRow)
                    .where(
                        EquipmentRow.equipment_id == equipment.equipment_id,
                        EquipmentRow.version == expected_stored_version,
                    )
                    .values(**_equipment_values(equipment, include_id=False))
                )
                if getattr(result, "rowcount", 0) != 1:
                    raise InvalidTransition("optimistic lock conflict")
                session.add(
                    EquipmentTelemetryRow(
                        sample_id=sample.sample_id,
                        equipment_id=equipment.equipment_id,
                        observed_at=sample.observed_at,
                        state=sample.state.value,
                        spindle_load_percent=sample.spindle_load_percent,
                        temperature_celsius=sample.temperature_celsius,
                        alarm_code=sample.alarm_code,
                        downtime_reason=sample.downtime_reason,
                    )
                )
                session.add_all(_event_rows([event]))
        except IntegrityError as exc:
            raise IdempotencyConflict("telemetry sample already exists") from exc

    def get_agent_proposal(self, proposal_id: str) -> AgentProposal | None:
        with self._sessions() as session:
            row = session.get(AgentProposalRow, proposal_id)
            return _proposal_to_domain(row) if row else None

    def get_agent_proposal_by_fingerprint(self, fingerprint: str) -> AgentProposal | None:
        with self._sessions() as session:
            row = session.scalar(
                select(AgentProposalRow).where(AgentProposalRow.fingerprint == fingerprint)
            )
            return _proposal_to_domain(row) if row else None

    def list_agent_proposals(self, limit: int = 100) -> list[AgentProposal]:
        with self._sessions() as session:
            rows = session.scalars(
                select(AgentProposalRow).order_by(AgentProposalRow.updated_at.desc()).limit(limit)
            ).all()
            return [_proposal_to_domain(row) for row in rows]

    def add_agent_proposal_atomically(self, proposal: AgentProposal, event: DomainEvent) -> None:
        try:
            with self._sessions.begin() as session:
                session.add(AgentProposalRow(**_proposal_values(proposal)))
                session.add_all(_event_rows([event]))
        except IntegrityError:
            return

    def update_agent_proposal_atomically(
        self, proposal: AgentProposal, expected_status: ProposalStatus, event: DomainEvent
    ) -> None:
        with self._sessions.begin() as session:
            result = session.execute(
                update(AgentProposalRow)
                .where(
                    AgentProposalRow.proposal_id == proposal.proposal_id,
                    AgentProposalRow.status == expected_status.value,
                )
                .values(**_proposal_values(proposal, include_id=False))
            )
            if getattr(result, "rowcount", 0) != 1:
                raise InvalidTransition("agent proposal status changed")
            session.add_all(_event_rows([event]))

    def get_inspection(self, inspection_id: str) -> QualityInspection | None:
        with self._sessions() as session:
            row = session.get(QualityInspectionRow, inspection_id)
            return _inspection_to_domain(row) if row else None

    def list_inspections(self, limit: int = 100) -> list[QualityInspection]:
        with self._sessions() as session:
            rows = session.scalars(
                select(QualityInspectionRow)
                .order_by(QualityInspectionRow.updated_at.desc())
                .limit(limit)
            ).all()
            return [_inspection_to_domain(row) for row in rows]

    def add_inspection_atomically(self, inspection: QualityInspection, event: DomainEvent) -> None:
        with self._sessions.begin() as session:
            session.add(QualityInspectionRow(**_inspection_values(inspection)))
            session.add_all(_event_rows([event]))

    def update_inspection_atomically(
        self, inspection: QualityInspection, expected_version: int, event: DomainEvent
    ) -> None:
        with self._sessions.begin() as session:
            result = session.execute(
                update(QualityInspectionRow)
                .where(
                    QualityInspectionRow.inspection_id == inspection.inspection_id,
                    QualityInspectionRow.version == expected_version,
                )
                .values(**_inspection_values(inspection, include_id=False))
            )
            if getattr(result, "rowcount", 0) != 1:
                raise InvalidTransition("quality inspection version changed")
            session.add_all(_event_rows([event]))

    def get_product_unit(self, product_serial: str) -> ProductUnit | None:
        with self._sessions() as session:
            row = session.get(ProductUnitRow, product_serial)
            return _product_unit_to_domain(row) if row else None

    def list_genealogy_links(self, product_serial: str) -> list[GenealogyLink]:
        with self._sessions() as session:
            rows = session.scalars(
                select(GenealogyLinkRow)
                .where(GenealogyLinkRow.product_serial == product_serial)
                .order_by(GenealogyLinkRow.occurred_at, GenealogyLinkRow.link_id)
            ).all()
            return [_genealogy_link_to_domain(row) for row in rows]

    def add_product_unit_atomically(
        self, unit: ProductUnit, links: list[GenealogyLink], event: DomainEvent
    ) -> None:
        try:
            with self._sessions.begin() as session:
                session.add(
                    ProductUnitRow(
                        product_serial=unit.product_serial,
                        work_order_id=unit.work_order_id,
                        product_revision_id=unit.product_revision_id,
                        genealogy_status=unit.genealogy_status,
                        created_at=unit.created_at,
                    )
                )
                session.flush()
                session.add_all(
                    GenealogyLinkRow(
                        link_id=item.link_id,
                        product_serial=item.product_serial,
                        relation_type=item.relation_type,
                        object_type=item.object_type,
                        object_id=item.object_id,
                        operation_sequence=item.operation_sequence,
                        occurred_at=item.occurred_at,
                    )
                    for item in links
                )
                session.add_all(_event_rows([event]))
        except IntegrityError as exc:
            raise IdempotencyConflict("product serial already exists") from exc

    def list_execution_sessions(self, product_serial: str) -> list[ExecutionSession]:
        with self._sessions() as session:
            rows = session.scalars(
                select(ExecutionSessionRow)
                .where(ExecutionSessionRow.product_serial == product_serial)
                .order_by(ExecutionSessionRow.started_at)
            ).all()
            return [_execution_session_to_domain(row) for row in rows]

    def list_material_consumptions(self, session_id: str) -> list[MaterialConsumption]:
        with self._sessions() as session:
            rows = session.scalars(
                select(MaterialConsumptionRow)
                .where(MaterialConsumptionRow.session_id == session_id)
                .order_by(MaterialConsumptionRow.recorded_at)
            ).all()
            return [_material_consumption_to_domain(row) for row in rows]

    def add_execution_session_atomically(
        self,
        execution: ExecutionSession,
        materials: list[MaterialConsumption],
        links: list[GenealogyLink],
        event: DomainEvent,
    ) -> None:
        try:
            with self._sessions.begin() as session:
                session.add(
                    ExecutionSessionRow(
                        session_id=execution.session_id,
                        product_serial=execution.product_serial,
                        work_order_id=execution.work_order_id,
                        operation_sequence=execution.operation_sequence,
                        operator_id=execution.operator_id,
                        equipment_id=execution.equipment_id,
                        started_at=execution.started_at,
                        ended_at=execution.ended_at,
                        created_at=execution.created_at,
                        resource_context=[
                            {
                                "resource_type": item.resource_type,
                                "resource_id": item.resource_id,
                                "revision": item.revision,
                                "status": item.status,
                                "life_remaining_percent": item.life_remaining_percent,
                            }
                            for item in execution.resources
                        ],
                    )
                )
                session.flush()
                session.add_all(
                    MaterialConsumptionRow(
                        consumption_id=item.consumption_id,
                        session_id=item.session_id,
                        material_lot=item.material_lot,
                        quantity=item.quantity,
                        unit=item.unit,
                        recorded_at=item.recorded_at,
                    )
                    for item in materials
                )
                session.add_all(
                    GenealogyLinkRow(
                        link_id=item.link_id,
                        product_serial=item.product_serial,
                        relation_type=item.relation_type,
                        object_type=item.object_type,
                        object_id=item.object_id,
                        operation_sequence=item.operation_sequence,
                        occurred_at=item.occurred_at,
                    )
                    for item in links
                )
                session.add_all(_event_rows([event]))
        except IntegrityError as exc:
            raise IdempotencyConflict("execution session or genealogy fact already exists") from exc

    def get_manufacturing_resource(
        self, resource_type: str, resource_id: str, revision: str = ""
    ) -> ManufacturingResource | None:
        key = f"{resource_type.strip().upper()}:{resource_id.strip().upper()}:{revision.strip().upper()}"
        with self._sessions() as session:
            row = session.get(ManufacturingResourceRow, key)
            return _manufacturing_resource_to_domain(row) if row else None

    def list_manufacturing_resources(self, limit: int = 100) -> list[ManufacturingResource]:
        with self._sessions() as session:
            rows = session.scalars(
                select(ManufacturingResourceRow)
                .order_by(ManufacturingResourceRow.updated_at.desc())
                .limit(limit)
            ).all()
            return [_manufacturing_resource_to_domain(row) for row in rows]

    def add_manufacturing_resource_atomically(
        self, resource: ManufacturingResource, event: DomainEvent
    ) -> None:
        try:
            with self._sessions.begin() as session:
                session.add(
                    ManufacturingResourceRow(
                        resource_key=resource.key,
                        resource_type=resource.resource_type,
                        resource_id=resource.resource_id,
                        revision=resource.revision,
                        name=resource.name,
                        status=resource.status,
                        life_remaining_percent=resource.life_remaining_percent,
                        calibration_due_at=resource.calibration_due_at,
                        source_system=resource.source_system,
                        external_reference=resource.external_reference,
                        source_updated_at=resource.source_updated_at,
                        version=resource.version,
                        created_at=resource.created_at,
                        updated_at=resource.updated_at,
                    )
                )
                session.add_all(_event_rows([event]))
        except IntegrityError as exc:
            raise IdempotencyConflict("manufacturing resource already exists") from exc

    def update_manufacturing_resource_atomically(
        self, resource: ManufacturingResource, expected_version: int, event: DomainEvent
    ) -> None:
        with self._sessions.begin() as session:
            result = session.execute(
                update(ManufacturingResourceRow)
                .where(
                    ManufacturingResourceRow.resource_key == resource.key,
                    ManufacturingResourceRow.version == expected_version,
                )
                .values(
                    status=resource.status,
                    life_remaining_percent=resource.life_remaining_percent,
                    calibration_due_at=resource.calibration_due_at,
                    source_updated_at=resource.source_updated_at,
                    version=resource.version,
                    updated_at=resource.updated_at,
                )
            )
            if getattr(result, "rowcount", 0) != 1:
                raise InvalidTransition("manufacturing resource version changed")
            session.add_all(_event_rows([event]))

    def add_manufacturing_resources_atomically(
        self, resources: list[ManufacturingResource], events: list[DomainEvent]
    ) -> None:
        try:
            with self._sessions.begin() as session:
                session.add_all(
                    ManufacturingResourceRow(
                        resource_key=item.key,
                        resource_type=item.resource_type,
                        resource_id=item.resource_id,
                        revision=item.revision,
                        name=item.name,
                        status=item.status,
                        life_remaining_percent=item.life_remaining_percent,
                        calibration_due_at=item.calibration_due_at,
                        source_system=item.source_system,
                        external_reference=item.external_reference,
                        source_updated_at=item.source_updated_at,
                        version=item.version,
                        created_at=item.created_at,
                        updated_at=item.updated_at,
                    )
                    for item in resources
                )
                session.add_all(_event_rows(events))
        except IntegrityError as exc:
            raise IdempotencyConflict(
                "manufacturing resource batch contains duplicate key"
            ) from exc

    def record_connector_receipt(self, receipt: ConnectorReceipt) -> None:
        try:
            with self._sessions.begin() as session:
                session.add(
                    ConnectorReceiptRow(
                        nonce=receipt.nonce,
                        key_id=receipt.key_id,
                        request_digest=receipt.request_digest,
                        received_at=receipt.received_at,
                    )
                )
        except IntegrityError as exc:
            raise IdempotencyConflict("connector nonce was already used") from exc


def _row_values(item: WorkOrder) -> dict[str, Any]:
    return {
        "human_code": item.human_code,
        "production_order_id": item.production_order_id,
        "workshop_id": item.workshop_id,
        "quantity": item.quantity,
        "due_at": item.due_at,
        "priority": item.priority,
        "product_revision_id": item.revisions.product_revision_id,
        "routing_revision_id": item.revisions.routing_revision_id,
        "bom_revision_id": item.revisions.bom_revision_id,
        "drawing_revision_ids": list(item.revisions.drawing_revision_ids),
        "operations": [_operation_to_dict(operation) for operation in item.operations],
        "status": item.status.value,
        "version": item.version,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _to_row(item: WorkOrder) -> WorkOrderRow:
    return WorkOrderRow(work_order_id=item.work_order_id, **_row_values(item))


def _to_domain(row: WorkOrderRow) -> WorkOrder:
    return WorkOrder(
        work_order_id=row.work_order_id,
        human_code=row.human_code,
        production_order_id=row.production_order_id,
        workshop_id=row.workshop_id,
        quantity=row.quantity,
        due_at=row.due_at,
        priority=row.priority,
        revisions=FrozenRevisions(
            product_revision_id=row.product_revision_id,
            routing_revision_id=row.routing_revision_id,
            bom_revision_id=row.bom_revision_id,
            drawing_revision_ids=list(row.drawing_revision_ids),
        ),
        status=WorkOrderStatus(row.status),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        operations=[_operation_from_dict(item) for item in row.operations],
    )


def _operation_to_dict(item: ProductionOperation) -> dict[str, Any]:
    return {
        "sequence": item.sequence,
        "operationCode": item.operation_code,
        "operationName": item.operation_name,
        "workCenterId": item.work_center_id,
        "plannedQuantity": item.planned_quantity,
        "status": item.status.value,
        "assignedResourceId": item.assigned_resource_id,
        "goodQuantity": item.good_quantity,
        "scrapQuantity": item.scrap_quantity,
    }


def _operation_from_dict(item: dict[str, Any]) -> ProductionOperation:
    return ProductionOperation(
        sequence=int(item["sequence"]),
        operation_code=str(item["operationCode"]),
        operation_name=str(item["operationName"]),
        work_center_id=str(item["workCenterId"]),
        planned_quantity=int(item["plannedQuantity"]),
        status=OperationStatus(str(item.get("status", "PENDING"))),
        assigned_resource_id=item.get("assignedResourceId"),
        good_quantity=int(item.get("goodQuantity", 0)),
        scrap_quantity=int(item.get("scrapQuantity", 0)),
    )


def _event_rows(events: list[DomainEvent]) -> list[EventOutboxRow]:
    return [
        EventOutboxRow(
            event_id=event.event_id,
            event_type=event.event_type,
            schema_version=event.schema_version,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            occurred_at=event.occurred_at,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            payload=event.payload,
            publish_status="PENDING",
            attempts=0,
        )
        for event in events
    ]


def _equipment_values(item: Equipment, *, include_id: bool = True) -> dict[str, Any]:
    values: dict[str, Any] = {
        "code": item.code,
        "name": item.name,
        "workshop_id": item.workshop_id,
        "work_center_id": item.work_center_id,
        "protocol": item.protocol,
        "state": item.state.value,
        "version": item.version,
        "last_seen_at": item.last_seen_at,
        "spindle_load_percent": item.spindle_load_percent,
        "temperature_celsius": item.temperature_celsius,
        "alarm_code": item.alarm_code,
        "downtime_reason": item.downtime_reason,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    if include_id:
        values["equipment_id"] = item.equipment_id
    return values


def _equipment_to_domain(row: EquipmentRow) -> Equipment:
    return Equipment(
        equipment_id=row.equipment_id,
        code=row.code,
        name=row.name,
        workshop_id=row.workshop_id,
        work_center_id=row.work_center_id,
        protocol=row.protocol,
        state=EquipmentState(row.state),
        version=row.version,
        last_seen_at=row.last_seen_at,
        spindle_load_percent=row.spindle_load_percent,
        temperature_celsius=row.temperature_celsius,
        alarm_code=row.alarm_code,
        downtime_reason=row.downtime_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _proposal_values(item: AgentProposal, *, include_id: bool = True) -> dict[str, Any]:
    values: dict[str, Any] = {
        "fingerprint": item.fingerprint,
        "agent_id": item.agent_id,
        "action": item.action,
        "risk": item.risk,
        "status": item.status.value,
        "work_order_id": item.work_order_id,
        "work_order_version": item.work_order_version,
        "operation_sequence": item.operation_sequence,
        "equipment_id": item.equipment_id,
        "equipment_version": item.equipment_version,
        "diagnosis": item.diagnosis,
        "rationale": item.rationale,
        "narrative_source": item.narrative_source,
        "model_name": item.model_name,
        "approved_by": item.approved_by,
        "approval_reason": item.approval_reason,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }
    if include_id:
        values["proposal_id"] = item.proposal_id
    return values


def _proposal_to_domain(row: AgentProposalRow) -> AgentProposal:
    return AgentProposal(
        proposal_id=row.proposal_id,
        fingerprint=row.fingerprint,
        agent_id=row.agent_id,
        action=row.action,
        risk=row.risk,
        status=ProposalStatus(row.status),
        work_order_id=row.work_order_id,
        work_order_version=row.work_order_version,
        operation_sequence=row.operation_sequence,
        equipment_id=row.equipment_id,
        equipment_version=row.equipment_version,
        diagnosis=row.diagnosis,
        rationale=row.rationale,
        narrative_source=row.narrative_source,
        model_name=row.model_name,
        approved_by=row.approved_by,
        approval_reason=row.approval_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _inspection_values(item: QualityInspection, *, include_id: bool = True) -> dict[str, Any]:
    values = {
        "work_order_id": item.work_order_id,
        "operation_sequence": item.operation_sequence,
        "sample_size": item.sample_size,
        "status": item.status.value,
        "version": item.version,
        "result": item.result,
        "defect_code": item.defect_code,
        "notes": item.notes,
        "rework_route": list(item.rework_route),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "gauge_id": item.gauge_id,
        "calibration_due_at": item.calibration_due_at,
        "measurement_recorded_at": item.measurement_recorded_at,
    }
    if include_id:
        values["inspection_id"] = item.inspection_id
    return values


def _inspection_to_domain(row: QualityInspectionRow) -> QualityInspection:
    return QualityInspection(
        row.inspection_id,
        row.work_order_id,
        row.operation_sequence,
        row.sample_size,
        InspectionStatus(row.status),
        row.version,
        row.result,
        row.defect_code,
        row.notes,
        list(row.rework_route),
        row.created_at,
        row.updated_at,
        row.gauge_id,
        row.calibration_due_at,
        row.measurement_recorded_at,
    )


def _product_unit_to_domain(row: ProductUnitRow) -> ProductUnit:
    return ProductUnit(
        row.product_serial,
        row.work_order_id,
        row.product_revision_id,
        row.genealogy_status,
        row.created_at,
    )


def _genealogy_link_to_domain(row: GenealogyLinkRow) -> GenealogyLink:
    return GenealogyLink(
        row.link_id,
        row.product_serial,
        row.relation_type,
        row.object_type,
        row.object_id,
        row.operation_sequence,
        row.occurred_at,
    )


def _execution_session_to_domain(row: ExecutionSessionRow) -> ExecutionSession:
    from autonomous_mes.domain.genealogy import ProcessResourceEvidence

    return ExecutionSession(
        row.session_id,
        row.product_serial,
        row.work_order_id,
        row.operation_sequence,
        row.operator_id,
        row.equipment_id,
        row.started_at,
        row.ended_at,
        row.created_at,
        tuple(ProcessResourceEvidence(**item) for item in row.resource_context),
    )


def _material_consumption_to_domain(row: MaterialConsumptionRow) -> MaterialConsumption:
    return MaterialConsumption(
        row.consumption_id,
        row.session_id,
        row.material_lot,
        row.quantity,
        row.unit,
        row.recorded_at,
    )


def _manufacturing_resource_to_domain(row: ManufacturingResourceRow) -> ManufacturingResource:
    return ManufacturingResource(
        row.resource_type,
        row.resource_id,
        row.revision,
        row.name,
        row.status,
        row.life_remaining_percent,
        row.calibration_due_at,
        row.source_system,
        row.external_reference,
        row.source_updated_at,
        row.version,
        row.created_at,
        row.updated_at,
    )
