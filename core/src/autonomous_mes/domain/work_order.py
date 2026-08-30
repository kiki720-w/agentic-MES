from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import List
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


@dataclass(frozen=True)
class FrozenRevisions:
    product_revision_id: str
    routing_revision_id: str
    bom_revision_id: str
    drawing_revision_ids: List[str]

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
    pending_events: List[DomainEvent] = field(default_factory=list, compare=False)

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
            pending_events=[event],
        )

    def release(self, expected_version: int, actor_id: str, correlation_id: str) -> "WorkOrder":
        if expected_version != self.version:
            raise InvalidTransition(
                "work order version changed; reload before attempting release"
            )
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

