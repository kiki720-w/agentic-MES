from dataclasses import dataclass
from typing import Any

from autonomous_mes.domain.errors import NotFound, ValidationError
from autonomous_mes.domain.quality import QualityInspection

from .ports import QualityStore, WorkOrderStore


@dataclass(frozen=True)
class CreateInspectionCommand:
    correlation_id: str
    work_order_id: str
    operation_sequence: int
    sample_size: int
    actor_id: str


class QualityApplicationService:
    def __init__(self, store: QualityStore, orders: WorkOrderStore) -> None:
        self._store, self._orders = store, orders

    def create(self, command: CreateInspectionCommand) -> dict[str, Any]:
        order = self._orders.get(command.work_order_id)
        if order is None:
            raise NotFound("work order not found")
        operation = next(
            (x for x in order.operations if x.sequence == command.operation_sequence), None
        )
        if operation is None or operation.status.value != "COMPLETED":
            raise ValidationError("inspection requires a completed operation")
        item = QualityInspection.create(
            command.work_order_id, command.operation_sequence, command.sample_size
        )
        self._store.add_inspection_atomically(
            item, item.event("QualityInspectionCreated", command.correlation_id, command.actor_id)
        )
        return _serialize(item)

    def record(
        self,
        inspection_id: str,
        passed: bool,
        defect_code: str | None,
        notes: str | None,
        expected_version: int,
        actor_id: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        current = self._require(inspection_id)
        changed = current.record(passed, defect_code, notes, expected_version)
        self._store.update_inspection_atomically(
            changed,
            current.version,
            changed.event(
                "QualityInspectionPassed" if passed else "ProductQuarantined",
                correlation_id,
                actor_id,
            ),
        )
        return _serialize(changed)

    def approve_rework(
        self,
        inspection_id: str,
        route: list[str],
        expected_version: int,
        actor_id: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        current = self._require(inspection_id)
        changed = current.approve_rework(route, expected_version)
        self._store.update_inspection_atomically(
            changed, current.version, changed.event("ReworkRouteApproved", correlation_id, actor_id)
        )
        return _serialize(changed)

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValidationError("limit must be between 1 and 500")
        return [_serialize(x) for x in self._store.list_inspections(limit)]

    def _require(self, inspection_id: str) -> QualityInspection:
        item = self._store.get_inspection(inspection_id)
        if item is None:
            raise NotFound("quality inspection not found")
        return item


def _serialize(x: QualityInspection) -> dict[str, Any]:
    return {
        "inspectionId": x.inspection_id,
        "workOrderId": x.work_order_id,
        "operationSequence": x.operation_sequence,
        "sampleSize": x.sample_size,
        "status": x.status.value,
        "version": x.version,
        "result": x.result,
        "defectCode": x.defect_code,
        "notes": x.notes,
        "reworkRoute": x.rework_route,
        "createdAt": x.created_at.isoformat(),
        "updatedAt": x.updated_at.isoformat(),
    }
