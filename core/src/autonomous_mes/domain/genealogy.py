from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from .errors import ValidationError
from .events import DomainEvent, utc_now


@dataclass(frozen=True)
class GenealogyLink:
    link_id: str
    product_serial: str
    relation_type: str
    object_type: str
    object_id: str
    operation_sequence: int | None
    occurred_at: datetime


@dataclass(frozen=True)
class ProductUnit:
    product_serial: str
    work_order_id: str
    product_revision_id: str
    genealogy_status: str
    created_at: datetime

    @classmethod
    def create(
        cls, product_serial: str, work_order_id: str, product_revision_id: str
    ) -> "ProductUnit":
        serial = product_serial.strip().upper()
        if not serial or len(serial) > 96:
            raise ValidationError("product serial must contain 1 to 96 characters")
        return cls(serial, work_order_id, product_revision_id, "ACTIVE", utc_now())

    def link(
        self,
        relation_type: str,
        object_type: str,
        object_id: str,
        operation_sequence: int | None = None,
    ) -> GenealogyLink:
        return GenealogyLink(
            str(uuid4()),
            self.product_serial,
            relation_type,
            object_type,
            object_id,
            operation_sequence,
            utc_now(),
        )

    def event(self, links: list[GenealogyLink], correlation_id: str, actor_id: str) -> DomainEvent:
        return DomainEvent.create(
            "ProductUnitRegistered",
            "ProductUnit",
            self.product_serial,
            {
                "workOrderId": self.work_order_id,
                "productRevisionId": self.product_revision_id,
                "linkCount": len(links),
                "actorId": actor_id,
            },
            correlation_id,
        )


@dataclass(frozen=True)
class MaterialConsumption:
    consumption_id: str
    session_id: str
    material_lot: str
    quantity: float
    unit: str
    recorded_at: datetime


@dataclass(frozen=True)
class ExecutionSession:
    session_id: str
    product_serial: str
    work_order_id: str
    operation_sequence: int
    operator_id: str
    equipment_id: str
    started_at: datetime
    ended_at: datetime
    created_at: datetime

    @classmethod
    def create(
        cls,
        session_id: str,
        product_serial: str,
        work_order_id: str,
        operation_sequence: int,
        operator_id: str,
        equipment_id: str,
        started_at: datetime,
        ended_at: datetime,
    ) -> "ExecutionSession":
        if not operator_id.strip():
            raise ValidationError("operator id is required")
        if not session_id.strip() or len(session_id) > 96:
            raise ValidationError("source session id must contain 1 to 96 characters")
        if ended_at < started_at:
            raise ValidationError("execution end must not precede start")
        return cls(
            session_id.strip(),
            product_serial,
            work_order_id,
            operation_sequence,
            operator_id.strip(),
            equipment_id,
            started_at,
            ended_at,
            utc_now(),
        )

    def consume(self, material_lot: str, quantity: float, unit: str) -> MaterialConsumption:
        if not material_lot.strip() or quantity <= 0 or not unit.strip():
            raise ValidationError("material lot, positive quantity and unit are required")
        return MaterialConsumption(
            str(uuid4()),
            self.session_id,
            material_lot.strip().upper(),
            quantity,
            unit.strip().upper(),
            utc_now(),
        )

    def event(
        self,
        materials: list[MaterialConsumption],
        correlation_id: str,
    ) -> DomainEvent:
        return DomainEvent.create(
            "ExecutionSessionRecorded",
            "ExecutionSession",
            self.session_id,
            {
                "productSerial": self.product_serial,
                "workOrderId": self.work_order_id,
                "operationSequence": self.operation_sequence,
                "operatorId": self.operator_id,
                "equipmentId": self.equipment_id,
                "materialLots": [item.material_lot for item in materials],
            },
            correlation_id,
        )
