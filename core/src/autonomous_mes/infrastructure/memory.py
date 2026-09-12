from copy import deepcopy
from threading import RLock
from typing import Any

from autonomous_mes.application.ports import IdempotentResult
from autonomous_mes.domain.equipment import Equipment, TelemetrySample
from autonomous_mes.domain.errors import Forbidden, IdempotencyConflict, InvalidTransition
from autonomous_mes.domain.events import DomainEvent
from autonomous_mes.domain.work_order import WorkOrder


class InMemoryWorkOrderStore:
    """Atomic test adapter; PostgreSQL replaces this in the next slice."""

    def __init__(self) -> None:
        self._orders: dict[str, WorkOrder] = {}
        self._human_codes: dict[str, str] = {}
        self._outbox: list[dict[str, Any]] = []
        self._idempotency: dict[str, IdempotentResult] = {}
        self._lock = RLock()
        self._equipment: dict[str, Equipment] = {}
        self._equipment_codes: dict[str, str] = {}
        self._telemetry_samples: set[str] = set()

    def get(self, work_order_id: str) -> WorkOrder | None:
        with self._lock:
            item = self._orders.get(work_order_id)
            return deepcopy(item) if item else None

    def get_by_human_code(self, human_code: str) -> WorkOrder | None:
        with self._lock:
            work_order_id = self._human_codes.get(human_code)
            return self.get(work_order_id) if work_order_id else None

    def list_work_orders(self, limit: int = 100) -> list[WorkOrder]:
        with self._lock:
            items = sorted(self._orders.values(), key=lambda item: item.updated_at, reverse=True)
            return deepcopy(items[:limit])

    def get_idempotent_result(self, idempotency_key: str) -> IdempotentResult | None:
        with self._lock:
            return self._idempotency.get(idempotency_key)

    def save_atomically(
        self,
        work_order: WorkOrder,
        expected_stored_version: int | None,
        events: list[DomainEvent],
        idempotency_key: str,
        idempotent_result: IdempotentResult,
    ) -> IdempotentResult:
        with self._lock:
            prior = self._idempotency.get(idempotency_key)
            if prior:
                if prior != idempotent_result:
                    raise IdempotencyConflict("idempotency key payload conflicts")
                return prior

            stored = self._orders.get(work_order.work_order_id)
            if expected_stored_version is None and stored is not None:
                raise InvalidTransition("work order already exists")
            if expected_stored_version is not None and (
                stored is None or stored.version != expected_stored_version
            ):
                raise InvalidTransition("optimistic lock conflict")

            self._orders[work_order.work_order_id] = deepcopy(work_order)
            self._human_codes[work_order.human_code] = work_order.work_order_id
            for event in events:
                self._outbox.append(
                    {
                        "eventId": event.event_id,
                        "eventType": event.event_type,
                        "aggregateType": event.aggregate_type,
                        "aggregateId": event.aggregate_id,
                        "occurredAt": event.occurred_at.isoformat(),
                        "correlationId": event.correlation_id,
                        "causationId": event.causation_id,
                        "schemaVersion": event.schema_version,
                        "payload": deepcopy(event.payload),
                        "publishStatus": "PENDING",
                    }
                )
            self._idempotency[idempotency_key] = idempotent_result
            return idempotent_result

    def list_outbox(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self._outbox)

    def record_tool_event(self, event: DomainEvent) -> None:
        with self._lock:
            self._outbox.append(
                {
                    "eventId": event.event_id,
                    "eventType": event.event_type,
                    "aggregateType": event.aggregate_type,
                    "aggregateId": event.aggregate_id,
                    "occurredAt": event.occurred_at.isoformat(),
                    "correlationId": event.correlation_id,
                    "causationId": event.causation_id,
                    "schemaVersion": event.schema_version,
                    "payload": deepcopy(event.payload),
                    "publishStatus": "PENDING",
                }
            )

    def get_equipment(self, equipment_id: str) -> Equipment | None:
        with self._lock:
            item = self._equipment.get(equipment_id)
            return deepcopy(item) if item else None

    def get_equipment_by_code(self, code: str) -> Equipment | None:
        with self._lock:
            equipment_id = self._equipment_codes.get(code)
            return self.get_equipment(equipment_id) if equipment_id else None

    def list_equipment(self, limit: int = 100) -> list[Equipment]:
        with self._lock:
            items = sorted(self._equipment.values(), key=lambda item: item.updated_at, reverse=True)
            return deepcopy(items[:limit])

    def telemetry_sample_exists(self, sample_id: str) -> bool:
        with self._lock:
            return sample_id in self._telemetry_samples

    def add_equipment_atomically(self, equipment: Equipment, event: DomainEvent) -> None:
        with self._lock:
            if equipment.code in self._equipment_codes:
                raise IdempotencyConflict("equipment code already exists")
            self._equipment[equipment.equipment_id] = deepcopy(equipment)
            self._equipment_codes[equipment.code] = equipment.equipment_id
            self._append_event(event)

    def record_telemetry_atomically(
        self,
        equipment: Equipment,
        expected_stored_version: int,
        sample: TelemetrySample,
        event: DomainEvent,
    ) -> None:
        with self._lock:
            current = self._equipment.get(equipment.equipment_id)
            if current is None or current.version != expected_stored_version:
                raise InvalidTransition("optimistic lock conflict")
            if sample.sample_id in self._telemetry_samples:
                return
            self._equipment[equipment.equipment_id] = deepcopy(equipment)
            self._telemetry_samples.add(sample.sample_id)
            self._append_event(event)

    def _append_event(self, event: DomainEvent) -> None:
        self._outbox.append(
            {
                "eventId": event.event_id,
                "eventType": event.event_type,
                "aggregateType": event.aggregate_type,
                "aggregateId": event.aggregate_id,
                "occurredAt": event.occurred_at.isoformat(),
                "correlationId": event.correlation_id,
                "causationId": event.causation_id,
                "schemaVersion": event.schema_version,
                "payload": deepcopy(event.payload),
                "publishStatus": "PENDING",
            }
        )


class ScopedReadPolicy:
    def __init__(self, grants: dict[str, set[str]]) -> None:
        self._grants = grants

    def require_workshop_read(self, subject_id: str, workshop_id: str) -> None:
        if workshop_id not in self._grants.get(subject_id, set()):
            raise Forbidden("subject is not authorized for this workshop")
