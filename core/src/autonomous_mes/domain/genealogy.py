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
