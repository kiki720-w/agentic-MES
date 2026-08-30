from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List

from autonomous_mes.domain.errors import IdempotencyConflict, NotFound
from autonomous_mes.domain.work_order import FrozenRevisions, WorkOrder

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
    drawing_revision_ids: List[str]


@dataclass(frozen=True)
class ReleaseWorkOrderCommand:
    idempotency_key: str
    correlation_id: str
    work_order_id: str
    expected_version: int
    actor_id: str


class WorkOrderApplicationService:
    def __init__(self, store: WorkOrderStore) -> None:
        self._store = store

    def create(self, command: CreateWorkOrderCommand) -> Dict[str, Any]:
        prior = self._store.get_idempotent_result(command.idempotency_key)
        if prior:
            if prior.operation != "create_work_order":
                raise IdempotencyConflict("idempotency key was used for another operation")
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
        )
        result = IdempotentResult("create_work_order", work_order.work_order_id, work_order.version)
        self._store.save_atomically(
            work_order=work_order.clear_pending_events(),
            expected_stored_version=None,
            events=work_order.pending_events,
            idempotency_key=command.idempotency_key,
            idempotent_result=result,
        )
        return self.get(work_order.work_order_id)

    def release(self, command: ReleaseWorkOrderCommand) -> Dict[str, Any]:
        prior = self._store.get_idempotent_result(command.idempotency_key)
        if prior:
            if prior.operation != "release_work_order":
                raise IdempotencyConflict("idempotency key was used for another operation")
            return self.get(prior.resource_id)

        current = self._store.get(command.work_order_id)
        if current is None:
            raise NotFound("work order not found")
        released = current.release(
            expected_version=command.expected_version,
            actor_id=command.actor_id,
            correlation_id=command.correlation_id,
        )
        result = IdempotentResult("release_work_order", released.work_order_id, released.version)
        self._store.save_atomically(
            work_order=released.clear_pending_events(),
            expected_stored_version=current.version,
            events=released.pending_events,
            idempotency_key=command.idempotency_key,
            idempotent_result=result,
        )
        return self.get(released.work_order_id)

    def get(self, work_order_id: str) -> Dict[str, Any]:
        item = self._store.get(work_order_id)
        if item is None:
            raise NotFound("work order not found")
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
            "asOf": item.updated_at.isoformat(),
            "dataFreshness": "CURRENT",
            "sourceObjects": [{"type": "WorkOrder", "id": item.work_order_id}],
        }

