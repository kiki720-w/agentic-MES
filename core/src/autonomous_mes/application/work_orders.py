from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Any

from autonomous_mes.domain.errors import IdempotencyConflict, NotFound, ValidationError
from autonomous_mes.domain.work_order import FrozenRevisions, ProductionOperation, WorkOrder


@dataclass(frozen=True)
class OperationSpec:
    sequence: int
    operation_code: str
    operation_name: str
    work_center_id: str


from .ports import IdempotentResult, WorkOrderStore


@dataclass(frozen=True)
class CreateWorkOrderCommand:
    idempotency_key: str
    correlation_id: str
    human_code: str
    production_order_id: str
    workshop_id: str
    quantity: int
    due_at: datetime
    priority: int
    product_revision_id: str
    routing_revision_id: str
    bom_revision_id: str
    drawing_revision_ids: list[str]
    operations: list[OperationSpec] = field(default_factory=list)


@dataclass(frozen=True)
class ReleaseWorkOrderCommand:
    idempotency_key: str
    correlation_id: str
    work_order_id: str
    expected_version: int
    actor_id: str


@dataclass(frozen=True)
class OperationCommand:
    idempotency_key: str
    correlation_id: str
    work_order_id: str
    sequence: int
    expected_version: int
    actor_id: str
    action: str
    resource_id: str | None = None
    good_quantity: int = 0
    scrap_quantity: int = 0


StringList = list[str]


class WorkOrderApplicationService:
    def __init__(self, store: WorkOrderStore) -> None:
        self._store = store

    def create(self, command: CreateWorkOrderCommand) -> dict[str, Any]:
        request_hash = _command_hash(command)
        prior = self._store.get_idempotent_result(command.idempotency_key)
        if prior:
            _require_same_request(prior, "create_work_order", request_hash)
            return self.get(prior.resource_id)

        if self._store.get_by_human_code(command.human_code):
            raise IdempotencyConflict("work order human code already exists")

        work_order = WorkOrder.create(
            human_code=command.human_code,
            production_order_id=command.production_order_id,
            workshop_id=command.workshop_id,
            quantity=command.quantity,
            due_at=command.due_at,
            priority=command.priority,
            revisions=FrozenRevisions(
                product_revision_id=command.product_revision_id,
                routing_revision_id=command.routing_revision_id,
                bom_revision_id=command.bom_revision_id,
                drawing_revision_ids=command.drawing_revision_ids,
            ),
            correlation_id=command.correlation_id,
            operations=[
                ProductionOperation(
                    sequence=item.sequence,
                    operation_code=item.operation_code,
                    operation_name=item.operation_name,
                    work_center_id=item.work_center_id,
                    planned_quantity=command.quantity,
                )
                for item in command.operations
            ],
        )
        result = IdempotentResult(
            "create_work_order", work_order.work_order_id, work_order.version, request_hash
        )
        self._store.save_atomically(
            work_order=work_order.clear_pending_events(),
            expected_stored_version=None,
            events=work_order.pending_events,
            idempotency_key=command.idempotency_key,
            idempotent_result=result,
        )
        return self.get(work_order.work_order_id)

    def release(self, command: ReleaseWorkOrderCommand) -> dict[str, Any]:
        request_hash = _command_hash(command)
        prior = self._store.get_idempotent_result(command.idempotency_key)
        if prior:
            _require_same_request(prior, "release_work_order", request_hash)
            return self.get(prior.resource_id)

        current = self._store.get(command.work_order_id)
        if current is None:
            raise NotFound("work order not found")
        released = current.release(
            expected_version=command.expected_version,
            actor_id=command.actor_id,
            correlation_id=command.correlation_id,
        )
        result = IdempotentResult(
            "release_work_order", released.work_order_id, released.version, request_hash
        )
        self._store.save_atomically(
            work_order=released.clear_pending_events(),
            expected_stored_version=current.version,
            events=released.pending_events,
            idempotency_key=command.idempotency_key,
            idempotent_result=result,
        )
        return self.get(released.work_order_id)

    def get(self, work_order_id: str) -> dict[str, Any]:
        item = self._store.get(work_order_id)
        if item is None:
            raise NotFound("work order not found")
        return _serialize(item)

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        if limit < 1 or limit > 500:
            raise ValidationError("limit must be between 1 and 500")
        return [_serialize(item) for item in self._store.list_work_orders(limit)]

    def list_page(
        self,
        limit: int = 30,
        offset: int = 0,
        query: str | None = None,
        status: str | None = None,
        include_test: bool = False,
    ) -> dict[str, Any]:
        if limit < 1 or limit > 100:
            raise ValidationError("limit must be between 1 and 100")
        if offset < 0 or offset > 1_000_000:
            raise ValidationError("offset must be between 0 and 1000000")
        normalized_query = query.strip() if query else None
        normalized_status = status.strip().upper() if status else None
        items = self._store.list_work_orders(
            limit, offset, normalized_query, normalized_status, include_test
        )
        return {
            "items": [_serialize(item) for item in items],
            "count": len(items),
            "total": self._store.count_work_orders(
                normalized_query, normalized_status, include_test
            ),
            "limit": limit,
            "offset": offset,
        }

    def summary(self, include_test: bool = False) -> dict[str, Any]:
        statuses = self._store.summarize_work_orders(include_test)
        return {
            "total": sum(statuses.values()),
            "workInProgress": statuses.get("RELEASED", 0) + statuses.get("IN_PROGRESS", 0),
            "suspended": statuses.get("SUSPENDED", 0),
            "statusCounts": statuses,
        }

    def execute_operation(self, command: OperationCommand) -> dict[str, Any]:
        request_hash = _command_hash(command)
        operation_name = f"{command.action}_operation"
        prior = self._store.get_idempotent_result(command.idempotency_key)
        if prior:
            _require_same_request(prior, operation_name, request_hash)
            return self.get(prior.resource_id)

        current = self._store.get(command.work_order_id)
        if current is None:
            raise NotFound("work order not found")
        if command.action == "dispatch":
            changed = current.dispatch_operation(
                command.sequence,
                command.resource_id or "",
                command.actor_id,
                command.expected_version,
                command.correlation_id,
            )
        elif command.action == "start":
            changed = current.start_operation(
                command.sequence,
                command.actor_id,
                command.expected_version,
                command.correlation_id,
            )
        elif command.action == "report":
            changed = current.report_operation(
                command.sequence,
                command.good_quantity,
                command.scrap_quantity,
                command.actor_id,
                command.expected_version,
                command.correlation_id,
            )
        elif command.action == "complete":
            changed = current.complete_operation(
                command.sequence,
                command.actor_id,
                command.expected_version,
                command.correlation_id,
            )
        elif command.action == "resume":
            changed = current.resume_operation(
                command.sequence,
                command.actor_id,
                command.expected_version,
                command.correlation_id,
            )
        else:
            raise ValidationError("unsupported operation action")

        result = IdempotentResult(
            operation_name, changed.work_order_id, changed.version, request_hash
        )
        self._store.save_atomically(
            work_order=changed.clear_pending_events(),
            expected_stored_version=current.version,
            events=changed.pending_events,
            idempotency_key=command.idempotency_key,
            idempotent_result=result,
        )
        return self.get(changed.work_order_id)

    def handle_equipment_incident(
        self,
        equipment_id: str,
        equipment_code: str,
        incident_state: str,
        reason: str,
        sample_id: str,
    ) -> StringList:
        affected: StringList = []
        for current in self._store.list_work_orders(500):
            changed = current.suspend_for_equipment_incident(
                equipment_id,
                equipment_code,
                incident_state,
                reason,
                sample_id,
            )
            if changed is None:
                continue
            request_hash = sha256(
                f"{current.work_order_id}:{sample_id}:{incident_state}".encode()
            ).hexdigest()
            idempotency_key = f"equipment-incident:{sample_id}:{current.work_order_id}"
            prior = self._store.get_idempotent_result(idempotency_key)
            if prior is None:
                result = IdempotentResult(
                    "suspend_operation_for_equipment_incident",
                    current.work_order_id,
                    changed.version,
                    request_hash,
                )
                self._store.save_atomically(
                    changed.clear_pending_events(),
                    current.version,
                    changed.pending_events,
                    idempotency_key,
                    result,
                )
            affected.append(current.work_order_id)
        return affected


def _serialize(item: WorkOrder) -> dict[str, Any]:
    return {
        "workOrderId": item.work_order_id,
        "humanCode": item.human_code,
        "productionOrderId": item.production_order_id,
        "workshopId": item.workshop_id,
        "quantity": item.quantity,
        "dueAt": item.due_at.isoformat(),
        "priority": item.priority,
        "status": item.status.value,
        "version": item.version,
        "revisions": {
            "productRevisionId": item.revisions.product_revision_id,
            "routingRevisionId": item.revisions.routing_revision_id,
            "bomRevisionId": item.revisions.bom_revision_id,
            "drawingRevisionIds": list(item.revisions.drawing_revision_ids),
        },
        "operations": [
            {
                "sequence": operation.sequence,
                "operationCode": operation.operation_code,
                "operationName": operation.operation_name,
                "workCenterId": operation.work_center_id,
                "plannedQuantity": operation.planned_quantity,
                "status": operation.status.value,
                "assignedResourceId": operation.assigned_resource_id,
                "goodQuantity": operation.good_quantity,
                "scrapQuantity": operation.scrap_quantity,
            }
            for operation in item.operations
        ],
        "asOf": item.updated_at.isoformat(),
        "dataFreshness": "CURRENT",
        "sourceObjects": [{"type": "WorkOrder", "id": item.work_order_id}],
    }


def _command_hash(command: Any) -> str:
    payload = asdict(command)
    payload.pop("idempotency_key", None)
    payload.pop("correlation_id", None)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value),
    ).encode()
    return sha256(encoded).hexdigest()


def _require_same_request(prior: IdempotentResult, operation: str, request_hash: str) -> None:
    if prior.operation != operation or prior.request_hash != request_hash:
        raise IdempotencyConflict("idempotency key was used with a different request")
