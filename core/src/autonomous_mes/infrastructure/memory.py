from copy import deepcopy
from threading import RLock
from typing import Any

from autonomous_mes.application.connector_security import ConnectorReceipt
from autonomous_mes.application.ports import IdempotentResult
from autonomous_mes.domain.agent import AgentProposal, ProposalStatus
from autonomous_mes.domain.equipment import Equipment, TelemetrySample
from autonomous_mes.domain.errors import Forbidden, IdempotencyConflict, InvalidTransition
from autonomous_mes.domain.events import DomainEvent
from autonomous_mes.domain.genealogy import (
    ExecutionSession,
    GenealogyLink,
    MaterialConsumption,
    ProductUnit,
)
from autonomous_mes.domain.master_data import ManufacturingResource
from autonomous_mes.domain.quality import QualityInspection
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
        self._agent_proposals: dict[str, AgentProposal] = {}
        self._proposal_fingerprints: dict[str, str] = {}
        self._inspections: dict[str, QualityInspection] = {}
        self._product_units: dict[str, ProductUnit] = {}
        self._genealogy_links: dict[str, list[GenealogyLink]] = {}
        self._execution_sessions: dict[str, ExecutionSession] = {}
        self._material_consumptions: dict[str, list[MaterialConsumption]] = {}
        self._manufacturing_resources: dict[str, ManufacturingResource] = {}
        self._connector_nonces: set[str] = set()

    def get(self, work_order_id: str) -> WorkOrder | None:
        with self._lock:
            item = self._orders.get(work_order_id)
            return deepcopy(item) if item else None

    def get_by_human_code(self, human_code: str) -> WorkOrder | None:
        with self._lock:
            work_order_id = self._human_codes.get(human_code)
            return self.get(work_order_id) if work_order_id else None

    def list_work_orders(
        self,
        limit: int = 100,
        offset: int = 0,
        query: str | None = None,
        status: str | None = None,
        include_test: bool = False,
    ) -> list[WorkOrder]:
        with self._lock:
            items = sorted(self._orders.values(), key=lambda item: item.updated_at, reverse=True)
            items = self._filter_orders(items, query, status, include_test)
            return deepcopy(items[offset : offset + limit])

    def count_work_orders(
        self,
        query: str | None = None,
        status: str | None = None,
        include_test: bool = False,
    ) -> int:
        with self._lock:
            return len(
                self._filter_orders(list(self._orders.values()), query, status, include_test)
            )

    def summarize_work_orders(self, include_test: bool = False) -> dict[str, int]:
        with self._lock:
            summary: dict[str, int] = {}
            for item in self._orders.values():
                if not include_test and self._is_test_code(item.human_code):
                    continue
                summary[item.status.value] = summary.get(item.status.value, 0) + 1
            return summary

    @staticmethod
    def _filter_orders(
        items: list[WorkOrder], query: str | None, status: str | None, include_test: bool
    ) -> list[WorkOrder]:
        if not include_test:
            items = [item for item in items if not InMemoryWorkOrderStore._is_test_code(item.human_code)]
        if query:
            normalized = query.casefold()
            items = [item for item in items if normalized in item.human_code.casefold()]
        if status:
            items = [item for item in items if item.status.value == status]
        return items

    @staticmethod
    def _is_test_code(human_code: str) -> bool:
        return human_code.startswith(("WO-PG-TEST", "WO-PG-ROLLBACK"))

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

    def get_inspection(self, inspection_id: str) -> QualityInspection | None:
        with self._lock:
            return deepcopy(self._inspections.get(inspection_id))

    def list_inspections(self, limit: int = 100) -> list[QualityInspection]:
        with self._lock:
            items = sorted(self._inspections.values(), key=lambda x: x.updated_at, reverse=True)
            return deepcopy(items[:limit])

    def add_inspection_atomically(self, inspection: QualityInspection, event: DomainEvent) -> None:
        with self._lock:
            self._inspections[inspection.inspection_id] = deepcopy(inspection)
            self._append_event(event)

    def update_inspection_atomically(
        self, inspection: QualityInspection, expected_version: int, event: DomainEvent
    ) -> None:
        with self._lock:
            current = self._inspections.get(inspection.inspection_id)
            if current is None or current.version != expected_version:
                raise InvalidTransition("quality inspection version changed")
            self._inspections[inspection.inspection_id] = deepcopy(inspection)
            self._append_event(event)

    def get_agent_proposal(self, proposal_id: str) -> AgentProposal | None:
        with self._lock:
            item = self._agent_proposals.get(proposal_id)
            return deepcopy(item) if item else None

    def get_agent_proposal_by_fingerprint(self, fingerprint: str) -> AgentProposal | None:
        with self._lock:
            proposal_id = self._proposal_fingerprints.get(fingerprint)
            return self.get_agent_proposal(proposal_id) if proposal_id else None

    def list_agent_proposals(self, limit: int = 100) -> list[AgentProposal]:
        with self._lock:
            items = sorted(
                self._agent_proposals.values(), key=lambda item: item.updated_at, reverse=True
            )
            return deepcopy(items[:limit])

    def add_agent_proposal_atomically(self, proposal: AgentProposal, event: DomainEvent) -> None:
        with self._lock:
            if proposal.fingerprint in self._proposal_fingerprints:
                return
            self._agent_proposals[proposal.proposal_id] = deepcopy(proposal)
            self._proposal_fingerprints[proposal.fingerprint] = proposal.proposal_id
            self._append_event(event)

    def update_agent_proposal_atomically(
        self, proposal: AgentProposal, expected_status: ProposalStatus, event: DomainEvent
    ) -> None:
        with self._lock:
            current = self._agent_proposals.get(proposal.proposal_id)
            if current is None or current.status is not expected_status:
                raise InvalidTransition("agent proposal status changed")
            self._agent_proposals[proposal.proposal_id] = deepcopy(proposal)
            self._append_event(event)

    def get_product_unit(self, product_serial: str) -> ProductUnit | None:
        with self._lock:
            return deepcopy(self._product_units.get(product_serial))

    def list_genealogy_links(self, product_serial: str) -> list[GenealogyLink]:
        with self._lock:
            return deepcopy(self._genealogy_links.get(product_serial, []))

    def add_product_unit_atomically(
        self, unit: ProductUnit, links: list[GenealogyLink], event: DomainEvent
    ) -> None:
        with self._lock:
            if unit.product_serial in self._product_units:
                raise IdempotencyConflict("product serial already exists")
            self._product_units[unit.product_serial] = deepcopy(unit)
            self._genealogy_links[unit.product_serial] = deepcopy(links)
            self._append_event(event)

    def list_execution_sessions(self, product_serial: str) -> list[ExecutionSession]:
        with self._lock:
            return deepcopy(
                [
                    item
                    for item in self._execution_sessions.values()
                    if item.product_serial == product_serial
                ]
            )

    def list_material_consumptions(self, session_id: str) -> list[MaterialConsumption]:
        with self._lock:
            return deepcopy(self._material_consumptions.get(session_id, []))

    def add_execution_session_atomically(
        self,
        session: ExecutionSession,
        materials: list[MaterialConsumption],
        links: list[GenealogyLink],
        event: DomainEvent,
    ) -> None:
        with self._lock:
            if session.session_id in self._execution_sessions:
                raise IdempotencyConflict("execution session already exists")
            self._execution_sessions[session.session_id] = deepcopy(session)
            self._material_consumptions[session.session_id] = deepcopy(materials)
            self._genealogy_links.setdefault(session.product_serial, []).extend(deepcopy(links))
            self._append_event(event)

    def get_manufacturing_resource(
        self, resource_type: str, resource_id: str, revision: str = ""
    ) -> ManufacturingResource | None:
        key = f"{resource_type.strip().upper()}:{resource_id.strip().upper()}:{revision.strip().upper()}"
        with self._lock:
            return deepcopy(self._manufacturing_resources.get(key))

    def list_manufacturing_resources(self, limit: int = 100) -> list[ManufacturingResource]:
        with self._lock:
            items = sorted(
                self._manufacturing_resources.values(), key=lambda item: item.updated_at, reverse=True
            )
            return deepcopy(items[:limit])

    def add_manufacturing_resource_atomically(
        self, resource: ManufacturingResource, event: DomainEvent
    ) -> None:
        with self._lock:
            if resource.key in self._manufacturing_resources:
                raise IdempotencyConflict("manufacturing resource already exists")
            self._manufacturing_resources[resource.key] = deepcopy(resource)
            self._append_event(event)

    def update_manufacturing_resource_atomically(
        self, resource: ManufacturingResource, expected_version: int, event: DomainEvent
    ) -> None:
        with self._lock:
            current = self._manufacturing_resources.get(resource.key)
            if current is None or current.version != expected_version:
                raise InvalidTransition("manufacturing resource version changed")
            self._manufacturing_resources[resource.key] = deepcopy(resource)
            self._append_event(event)

    def add_manufacturing_resources_atomically(
        self, resources: list[ManufacturingResource], events: list[DomainEvent]
    ) -> None:
        with self._lock:
            keys = [item.key for item in resources]
            if len(keys) != len(set(keys)) or any(
                key in self._manufacturing_resources for key in keys
            ):
                raise IdempotencyConflict("manufacturing resource batch contains duplicate key")
            self._manufacturing_resources.update(
                {item.key: deepcopy(item) for item in resources}
            )
            for event in events:
                self._append_event(event)

    def record_connector_receipt(self, receipt: ConnectorReceipt) -> None:
        with self._lock:
            if receipt.nonce in self._connector_nonces:
                raise IdempotencyConflict("connector nonce was already used")
            self._connector_nonces.add(receipt.nonce)


class ScopedReadPolicy:
    def __init__(self, grants: dict[str, set[str]]) -> None:
        self._grants = grants

    def require_workshop_read(self, subject_id: str, workshop_id: str) -> None:
        if workshop_id not in self._grants.get(subject_id, set()):
            raise Forbidden("subject is not authorized for this workshop")
