from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from uuid import uuid4

from .errors import InvalidTransition, ValidationError
from .events import DomainEvent, utc_now


class WorkOrderStatus(str, Enum):
    DRAFT = "DRAFT"
    RELEASED = "RELEASED"
    IN_PROGRESS = "IN_PROGRESS"
    SUSPENDED = "SUSPENDED"
    COMPLETED = "COMPLETED"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class OperationStatus(str, Enum):
    PENDING = "PENDING"
    DISPATCHED = "DISPATCHED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True)
class ProductionOperation:
    sequence: int
    operation_code: str
    operation_name: str
    work_center_id: str
    planned_quantity: int
    status: OperationStatus = OperationStatus.PENDING
    assigned_resource_id: str | None = None
    good_quantity: int = 0
    scrap_quantity: int = 0

    def validate(self) -> None:
        if self.sequence < 10 or self.sequence % 10:
            raise ValidationError("operation sequence must be a positive multiple of 10")
        if any(
            not value.strip()
            for value in (self.operation_code, self.operation_name, self.work_center_id)
        ):
            raise ValidationError("operation code, name and work center are required")
        if self.planned_quantity <= 0:
            raise ValidationError("operation planned quantity must be greater than zero")


@dataclass(frozen=True)
class FrozenRevisions:
    product_revision_id: str
    routing_revision_id: str
    bom_revision_id: str
    drawing_revision_ids: list[str]

    def validate(self) -> None:
        required = (
            self.product_revision_id,
            self.routing_revision_id,
            self.bom_revision_id,
        )
        if any(not value.strip() for value in required):
            raise ValidationError("product, routing and BOM revisions are required")


@dataclass(frozen=True)
class WorkOrder:
    work_order_id: str
    human_code: str
    production_order_id: str
    workshop_id: str
    quantity: int
    due_at: datetime
    priority: int
    revisions: FrozenRevisions
    status: WorkOrderStatus
    version: int
    created_at: datetime
    updated_at: datetime
    operations: list[ProductionOperation]
    pending_events: list[DomainEvent] = field(default_factory=list, compare=False)

    @classmethod
    def create(
        cls,
        human_code: str,
        production_order_id: str,
        workshop_id: str,
        quantity: int,
        due_at: datetime,
        priority: int,
        revisions: FrozenRevisions,
        correlation_id: str,
        operations: list[ProductionOperation] | None = None,
    ) -> "WorkOrder":
        if not human_code.strip() or not production_order_id.strip() or not workshop_id.strip():
            raise ValidationError("human code, production order and workshop are required")
        if quantity <= 0:
            raise ValidationError("quantity must be greater than zero")
        if not 1 <= priority <= 100:
            raise ValidationError("priority must be between 1 and 100")
        if due_at.tzinfo is None:
            raise ValidationError("due_at must include a timezone")
        revisions.validate()
        route = operations or []
        if len({item.sequence for item in route}) != len(route):
            raise ValidationError("operation sequence must be unique")
        for operation in route:
            operation.validate()

        now = utc_now()
        work_order_id = str(uuid4())
        event = DomainEvent.create(
            event_type="WorkOrderCreated",
            aggregate_type="WorkOrder",
            aggregate_id=work_order_id,
            correlation_id=correlation_id,
            payload={
                "humanCode": human_code,
                "productionOrderId": production_order_id,
                "workshopId": workshop_id,
                "quantity": quantity,
                "dueAt": due_at.isoformat(),
                "priority": priority,
                "operations": [operation.sequence for operation in route],
            },
        )
        return cls(
            work_order_id=work_order_id,
            human_code=human_code,
            production_order_id=production_order_id,
            workshop_id=workshop_id,
            quantity=quantity,
            due_at=due_at,
            priority=priority,
            revisions=revisions,
            status=WorkOrderStatus.DRAFT,
            version=1,
            created_at=now,
            updated_at=now,
            operations=sorted(route, key=lambda item: item.sequence),
            pending_events=[event],
        )

    def release(self, expected_version: int, actor_id: str, correlation_id: str) -> "WorkOrder":
        if expected_version != self.version:
            raise InvalidTransition("work order version changed; reload before attempting release")
        if self.status is not WorkOrderStatus.DRAFT:
            raise InvalidTransition("only a DRAFT work order can be released")
        if not actor_id.strip():
            raise ValidationError("actor_id is required")

        event = DomainEvent.create(
            event_type="WorkOrderReleased",
            aggregate_type="WorkOrder",
            aggregate_id=self.work_order_id,
            correlation_id=correlation_id,
            payload={
                "actorId": actor_id,
                "frozenRevisions": {
                    "productRevisionId": self.revisions.product_revision_id,
                    "routingRevisionId": self.revisions.routing_revision_id,
                    "bomRevisionId": self.revisions.bom_revision_id,
                    "drawingRevisionIds": list(self.revisions.drawing_revision_ids),
                },
            },
        )
        return replace(
            self,
            status=WorkOrderStatus.RELEASED,
            version=self.version + 1,
            updated_at=utc_now(),
            pending_events=[event],
        )

    def clear_pending_events(self) -> "WorkOrder":
        return replace(self, pending_events=[])

    def dispatch_operation(
        self,
        sequence: int,
        resource_id: str,
        actor_id: str,
        expected_version: int,
        correlation_id: str,
    ) -> "WorkOrder":
        operation = self._operation_for_transition(sequence, expected_version)
        if self.status is not WorkOrderStatus.RELEASED:
            raise InvalidTransition("work order must be RELEASED before dispatch")
        if operation.status is not OperationStatus.PENDING:
            raise InvalidTransition("only a PENDING operation can be dispatched")
        if not resource_id.strip() or not actor_id.strip():
            raise ValidationError("resource_id and actor_id are required")
        earlier = [item for item in self.operations if item.sequence < sequence]
        if any(item.status is not OperationStatus.COMPLETED for item in earlier):
            raise InvalidTransition("previous operations must be completed first")
        changed = replace(
            operation,
            status=OperationStatus.DISPATCHED,
            assigned_resource_id=resource_id,
        )
        return self._with_operation(
            changed,
            "OperationDispatched",
            actor_id,
            correlation_id,
            {"resourceId": resource_id},
        )

    def start_operation(
        self, sequence: int, actor_id: str, expected_version: int, correlation_id: str
    ) -> "WorkOrder":
        operation = self._operation_for_transition(sequence, expected_version)
        if operation.status is not OperationStatus.DISPATCHED:
            raise InvalidTransition("only a DISPATCHED operation can be started")
        changed = replace(operation, status=OperationStatus.IN_PROGRESS)
        return self._with_operation(
            changed,
            "OperationStarted",
            actor_id,
            correlation_id,
            {},
            work_order_status=WorkOrderStatus.IN_PROGRESS,
        )

    def report_operation(
        self,
        sequence: int,
        good_quantity: int,
        scrap_quantity: int,
        actor_id: str,
        expected_version: int,
        correlation_id: str,
    ) -> "WorkOrder":
        operation = self._operation_for_transition(sequence, expected_version)
        if operation.status is not OperationStatus.IN_PROGRESS:
            raise InvalidTransition("only an IN_PROGRESS operation accepts production reports")
        if good_quantity < 0 or scrap_quantity < 0 or good_quantity + scrap_quantity <= 0:
            raise ValidationError("reported quantity must be positive and cannot be negative")
        if operation.good_quantity + operation.scrap_quantity + good_quantity + scrap_quantity > operation.planned_quantity:
            raise ValidationError("reported quantity exceeds operation planned quantity")
        changed = replace(
            operation,
            good_quantity=operation.good_quantity + good_quantity,
            scrap_quantity=operation.scrap_quantity + scrap_quantity,
        )
        return self._with_operation(
            changed,
            "OperationProductionReported",
            actor_id,
            correlation_id,
            {"goodQuantity": good_quantity, "scrapQuantity": scrap_quantity},
        )

    def complete_operation(
        self, sequence: int, actor_id: str, expected_version: int, correlation_id: str
    ) -> "WorkOrder":
        operation = self._operation_for_transition(sequence, expected_version)
        if operation.status is not OperationStatus.IN_PROGRESS:
            raise InvalidTransition("only an IN_PROGRESS operation can be completed")
        if operation.good_quantity + operation.scrap_quantity != operation.planned_quantity:
            raise InvalidTransition("reported quantity must equal planned quantity before completion")
        changed = replace(operation, status=OperationStatus.COMPLETED)
        all_completed = all(
            item.sequence == sequence or item.status is OperationStatus.COMPLETED
            for item in self.operations
        )
        return self._with_operation(
            changed,
            "OperationCompleted",
            actor_id,
            correlation_id,
            {},
            work_order_status=WorkOrderStatus.COMPLETED if all_completed else WorkOrderStatus.RELEASED,
        )

    def _operation_for_transition(
        self, sequence: int, expected_version: int
    ) -> ProductionOperation:
        if expected_version != self.version:
            raise InvalidTransition("work order version changed; reload before operation command")
        if not self.operations:
            raise InvalidTransition("work order has no frozen operations")
        operation = next((item for item in self.operations if item.sequence == sequence), None)
        if operation is None:
            raise InvalidTransition("operation was not found in the frozen route")
        return operation

    def _with_operation(
        self,
        changed: ProductionOperation,
        event_type: str,
        actor_id: str,
        correlation_id: str,
        detail: dict[str, object],
        work_order_status: WorkOrderStatus | None = None,
    ) -> "WorkOrder":
        if not actor_id.strip():
            raise ValidationError("actor_id is required")
        event = DomainEvent.create(
            event_type=event_type,
            aggregate_type="WorkOrder",
            aggregate_id=self.work_order_id,
            correlation_id=correlation_id,
            payload={
                "operationSequence": changed.sequence,
                "operationCode": changed.operation_code,
                "actorId": actor_id,
                **detail,
            },
        )
        return replace(
            self,
            status=work_order_status or self.status,
            version=self.version + 1,
            updated_at=utc_now(),
            operations=[changed if item.sequence == changed.sequence else item for item in self.operations],
            pending_events=[event],
        )
