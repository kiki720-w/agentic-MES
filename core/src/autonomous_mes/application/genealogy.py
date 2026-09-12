from dataclasses import dataclass
from datetime import datetime
from typing import Any

from autonomous_mes.domain.errors import InvalidTransition, NotFound
from autonomous_mes.domain.genealogy import (
    ExecutionSession,
    GenealogyLink,
    MaterialConsumption,
    ProductUnit,
)

from .ports import MesStore


@dataclass(frozen=True)
class RegisterProductUnitCommand:
    correlation_id: str
    product_serial: str
    work_order_id: str
    actor_id: str


@dataclass(frozen=True)
class MaterialLotInput:
    material_lot: str
    quantity: float
    unit: str


@dataclass(frozen=True)
class RecordExecutionSessionCommand:
    correlation_id: str
    session_id: str
    product_serial: str
    operation_sequence: int
    operator_id: str
    equipment_id: str
    started_at: datetime
    ended_at: datetime
    materials: list[MaterialLotInput]


class GenealogyApplicationService:
    def __init__(self, store: MesStore) -> None:
        self._store = store

    def register(self, command: RegisterProductUnitCommand) -> dict[str, Any]:
        order = self._store.get(command.work_order_id)
        if order is None:
            raise NotFound("work order not found")
        if order.status.value == "DRAFT":
            raise InvalidTransition("product unit cannot be registered against a draft work order")
        unit = ProductUnit.create(
            command.product_serial,
            order.work_order_id,
            order.revisions.product_revision_id,
        )
        links = [
            unit.link("PRODUCED_BY", "WorkOrder", order.work_order_id),
            unit.link("USES_VERSION", "ProductRevision", order.revisions.product_revision_id),
            unit.link("USES_VERSION", "RoutingRevision", order.revisions.routing_revision_id),
            unit.link("USES_VERSION", "BomRevision", order.revisions.bom_revision_id),
        ]
        links.extend(
            unit.link("USES_VERSION", "DrawingRevision", drawing_id)
            for drawing_id in order.revisions.drawing_revision_ids
        )
        for operation in order.operations:
            if operation.assigned_resource_id:
                links.append(
                    unit.link(
                        "PROCESSED_ON",
                        "Equipment",
                        operation.assigned_resource_id,
                        operation.sequence,
                    )
                )
                links.append(
                    unit.link(
                        "PASSED_OPERATION",
                        "OperationTask",
                        f"{order.work_order_id}:OP{operation.sequence}",
                        operation.sequence,
                    )
                )
        self._store.add_product_unit_atomically(
            unit, links, unit.event(links, command.correlation_id, command.actor_id)
        )
        return self.get(unit.product_serial)

    def record_execution(self, command: RecordExecutionSessionCommand) -> dict[str, Any]:
        serial = command.product_serial.strip().upper()
        unit = self._store.get_product_unit(serial)
        if unit is None:
            raise NotFound("product unit not found")
        order = self._store.get(unit.work_order_id)
        if order is None:
            raise NotFound("work order not found")
        operation = next(
            (item for item in order.operations if item.sequence == command.operation_sequence), None
        )
        if operation is None or operation.status.value != "COMPLETED":
            raise InvalidTransition("execution evidence requires a completed operation")
        if operation.assigned_resource_id != command.equipment_id:
            raise InvalidTransition("equipment does not match the dispatched operation")
        session = ExecutionSession.create(
            command.session_id,
            serial,
            unit.work_order_id,
            command.operation_sequence,
            command.operator_id,
            command.equipment_id,
            command.started_at,
            command.ended_at,
        )
        materials = [
            session.consume(item.material_lot, item.quantity, item.unit)
            for item in command.materials
        ]
        links = [
            ProductUnit.link(unit, "EXECUTED_BY", "Person", session.operator_id, command.operation_sequence),
            ProductUnit.link(
                unit, "HAS_EXECUTION", "ExecutionSession", session.session_id, command.operation_sequence
            ),
        ]
        links.extend(
            ProductUnit.link(
                unit, "CONSUMED_MATERIAL", "MaterialLot", item.material_lot, command.operation_sequence
            )
            for item in materials
        )
        self._store.add_execution_session_atomically(
            session, materials, links, session.event(materials, command.correlation_id)
        )
        return _serialize_session(session, materials)

    def get(self, product_serial: str) -> dict[str, Any]:
        serial = product_serial.strip().upper()
        unit = self._store.get_product_unit(serial)
        if unit is None:
            raise NotFound("product unit not found")
        links = self._store.list_genealogy_links(serial)
        inspections = [
            item
            for item in self._store.list_inspections(500)
            if item.work_order_id == unit.work_order_id
        ]
        sessions = self._store.list_execution_sessions(serial)
        return {
            "productSerial": unit.product_serial,
            "workOrderId": unit.work_order_id,
            "productRevisionId": unit.product_revision_id,
            "genealogyStatus": unit.genealogy_status,
            "createdAt": unit.created_at.isoformat(),
            "links": [_serialize_link(item) for item in links],
            "qualityInspections": [
                {
                    "inspectionId": item.inspection_id,
                    "operationSequence": item.operation_sequence,
                    "status": item.status.value,
                    "result": item.result,
                    "defectCode": item.defect_code,
                    "updatedAt": item.updated_at.isoformat(),
                }
                for item in inspections
            ],
            "executionSessions": [
                _serialize_session(item, self._store.list_material_consumptions(item.session_id))
                for item in sessions
            ],
        }


def _serialize_link(item: GenealogyLink) -> dict[str, Any]:
    return {
        "linkId": item.link_id,
        "relationType": item.relation_type,
        "objectType": item.object_type,
        "objectId": item.object_id,
        "operationSequence": item.operation_sequence,
        "occurredAt": item.occurred_at.isoformat(),
    }


def _serialize_session(
    item: ExecutionSession, materials: list[MaterialConsumption]
) -> dict[str, Any]:
    return {
        "sessionId": item.session_id,
        "operationSequence": item.operation_sequence,
        "operatorId": item.operator_id,
        "equipmentId": item.equipment_id,
        "startedAt": item.started_at.isoformat(),
        "endedAt": item.ended_at.isoformat(),
        "materials": [
            {
                "consumptionId": material.consumption_id,
                "materialLot": material.material_lot,
                "quantity": material.quantity,
                "unit": material.unit,
                "recordedAt": material.recorded_at.isoformat(),
            }
            for material in materials
        ],
    }
