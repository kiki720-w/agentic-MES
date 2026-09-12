from copy import deepcopy
from datetime import UTC, datetime, timedelta
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
from autonomous_mes.domain.quality_policy import QualityPolicyStatus, QualityRiskPolicy
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
        self._quality_policies: dict[str, QualityRiskPolicy] = {}

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
            items = [
                item for item in items if not InMemoryWorkOrderStore._is_test_code(item.human_code)
            ]
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

    def list_recent_outbox(
        self,
        limit: int = 100,
        offset: int = 0,
        before_occurred_at: datetime | None = None,
        before_event_id: str | None = None,
        query: str | None = None,
        publish_status: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            items = list(reversed(self._outbox))
            if query:
                needle = query.casefold()
                items = [
                    item
                    for item in items
                    if any(
                        needle in str(item.get(field) or "").casefold()
                        for field in (
                            "eventId",
                            "eventType",
                            "aggregateType",
                            "aggregateId",
                            "correlationId",
                        )
                    )
                ]
            if publish_status:
                items = [
                    item for item in items if item["publishStatus"] == publish_status
                ]
            if before_occurred_at and before_event_id:
                cursor = (before_occurred_at, before_event_id)
                items = [
                    item
                    for item in items
                    if (datetime.fromisoformat(item["occurredAt"]), item["eventId"]) < cursor
                ]
            return deepcopy(items[offset : offset + limit])

    def count_outbox(
        self, query: str | None = None, publish_status: str | None = None
    ) -> int:
        with self._lock:
            items = self._outbox
            if query:
                needle = query.casefold()
                items = [
                    item
                    for item in items
                    if any(
                        needle in str(item.get(field) or "").casefold()
                        for field in (
                            "eventId",
                            "eventType",
                            "aggregateType",
                            "aggregateId",
                            "correlationId",
                        )
                    )
                ]
            if publish_status:
                items = [
                    item for item in items if item["publishStatus"] == publish_status
                ]
            return len(items)

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

    def list_equipment(
        self,
        limit: int = 100,
        offset: int = 0,
        query: str | None = None,
        state: str | None = None,
    ) -> list[Equipment]:
        with self._lock:
            items = sorted(self._equipment.values(), key=lambda item: item.updated_at, reverse=True)
            if query:
                needle = query.casefold()
                items = [
                    item
                    for item in items
                    if needle in item.code.casefold() or needle in item.name.casefold()
                ]
            if state:
                items = [item for item in items if item.state.value == state]
            return deepcopy(items[offset : offset + limit])

    def count_equipment(self, query: str | None = None, state: str | None = None) -> int:
        with self._lock:
            items = list(self._equipment.values())
            if query:
                needle = query.casefold()
                items = [
                    item
                    for item in items
                    if needle in item.code.casefold() or needle in item.name.casefold()
                ]
            if state:
                items = [item for item in items if item.state.value == state]
            return len(items)

    def summarize_equipment(self) -> dict[str, int]:
        with self._lock:
            result: dict[str, int] = {}
            for item in self._equipment.values():
                result[item.state.value] = result.get(item.state.value, 0) + 1
            return result

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

    def get_inspection_for_operation(
        self, work_order_id: str, operation_sequence: int
    ) -> QualityInspection | None:
        with self._lock:
            item = next(
                (
                    inspection
                    for inspection in self._inspections.values()
                    if inspection.work_order_id == work_order_id
                    and inspection.operation_sequence == operation_sequence
                ),
                None,
            )
            return deepcopy(item) if item else None

    def list_inspections(
        self,
        limit: int = 100,
        offset: int = 0,
        query: str | None = None,
        status: str | None = None,
    ) -> list[QualityInspection]:
        with self._lock:
            items = sorted(self._inspections.values(), key=lambda x: x.updated_at, reverse=True)
            if query:
                needle = query.casefold()
                items = [
                    item
                    for item in items
                    if needle in item.work_order_id.casefold()
                    or needle in (item.defect_code or "").casefold()
                ]
            if status:
                items = [item for item in items if item.status.value == status]
            return deepcopy(items[offset : offset + limit])

    def count_inspections(self, query: str | None = None, status: str | None = None) -> int:
        with self._lock:
            items = list(self._inspections.values())
            if query:
                needle = query.casefold()
                items = [
                    item
                    for item in items
                    if needle in item.work_order_id.casefold()
                    or needle in (item.defect_code or "").casefold()
                ]
            if status:
                items = [item for item in items if item.status.value == status]
            return len(items)

    def summarize_inspections(self) -> dict[str, int]:
        with self._lock:
            result: dict[str, int] = {}
            for item in self._inspections.values():
                result[item.status.value] = result.get(item.status.value, 0) + 1
            return result

    def list_eligible_quality_operations(
        self,
        limit: int = 30,
        offset: int = 0,
        query: str | None = None,
        workshop_id: str | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(
                self._eligible_quality_operations(query, workshop_id)[offset : offset + limit]
            )

    def count_eligible_quality_operations(
        self,
        query: str | None = None,
        workshop_id: str | None = None,
    ) -> int:
        with self._lock:
            return len(self._eligible_quality_operations(query, workshop_id))

    def _eligible_quality_operations(
        self,
        query: str | None,
        workshop_id: str | None,
    ) -> list[dict[str, Any]]:
        inspected = {
            (item.work_order_id, item.operation_sequence) for item in self._inspections.values()
        }
        needle = query.casefold() if query else None
        results: list[dict[str, Any]] = []
        for order in sorted(
            self._orders.values(),
            key=lambda item: (item.updated_at, item.work_order_id),
            reverse=True,
        ):
            if workshop_id and order.workshop_id != workshop_id:
                continue
            for operation in sorted(order.operations, key=lambda item: item.sequence):
                if operation.status.value != "COMPLETED":
                    continue
                if (order.work_order_id, operation.sequence) in inspected:
                    continue
                searchable = (
                    f"{order.human_code} {order.production_order_id} "
                    f"{operation.operation_code} {operation.operation_name} "
                    f"{operation.work_center_id}"
                ).casefold()
                if needle and needle not in searchable:
                    continue
                results.append(
                    {
                        "workOrderId": order.work_order_id,
                        "humanCode": order.human_code,
                        "workOrderVersion": order.version,
                        "operationSequence": operation.sequence,
                        "operationCode": operation.operation_code,
                        "operationName": operation.operation_name,
                        "workCenterId": operation.work_center_id,
                        "plannedQuantity": operation.planned_quantity,
                    }
                )
        return results

    def inspect_operation_projection(self) -> dict[str, int]:
        with self._lock:
            count = sum(len(item.operations) for item in self._orders.values())
            return {
                "legacyCount": count,
                "normalizedCount": count,
                "mismatchCount": 0,
                "extraCount": 0,
            }

    def add_inspection_atomically(self, inspection: QualityInspection, event: DomainEvent) -> None:
        with self._lock:
            if any(
                item.work_order_id == inspection.work_order_id
                and item.operation_sequence == inspection.operation_sequence
                for item in self._inspections.values()
            ):
                raise InvalidTransition("inspection already exists for this operation")
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

    def create_inspection_from_proposal_atomically(
        self,
        inspection: QualityInspection,
        proposal: AgentProposal,
        expected_proposal_status: ProposalStatus,
        events: list[DomainEvent],
    ) -> None:
        with self._lock:
            current = self._agent_proposals.get(proposal.proposal_id)
            if current is None or current.status is not expected_proposal_status:
                raise InvalidTransition("agent proposal status changed")
            if any(
                item.work_order_id == inspection.work_order_id
                and item.operation_sequence == inspection.operation_sequence
                for item in self._inspections.values()
            ):
                raise InvalidTransition("inspection already exists for this operation")
            self._inspections[inspection.inspection_id] = deepcopy(inspection)
            self._agent_proposals[proposal.proposal_id] = deepcopy(proposal)
            for event in events:
                self._append_event(event)

    def get_agent_proposal(self, proposal_id: str) -> AgentProposal | None:
        with self._lock:
            item = self._agent_proposals.get(proposal_id)
            return deepcopy(item) if item else None

    def get_agent_proposal_by_fingerprint(self, fingerprint: str) -> AgentProposal | None:
        with self._lock:
            proposal_id = self._proposal_fingerprints.get(fingerprint)
            return self.get_agent_proposal(proposal_id) if proposal_id else None

    def get_quality_recommendation(
        self, work_order_id: str, operation_sequence: int
    ) -> AgentProposal | None:
        with self._lock:
            item = next(
                (
                    proposal
                    for proposal in self._agent_proposals.values()
                    if proposal.action == "CREATE_QUALITY_INSPECTION"
                    and proposal.work_order_id == work_order_id
                    and proposal.operation_sequence == operation_sequence
                ),
                None,
            )
            return deepcopy(item) if item else None

    def list_agent_proposals(self, limit: int = 100) -> list[AgentProposal]:
        with self._lock:
            items = sorted(
                self._agent_proposals.values(),
                key=lambda item: (
                    item.quality_risk_score is not None,
                    item.quality_risk_score or -1,
                    item.updated_at,
                ),
                reverse=True,
            )
            return deepcopy(items[:limit])

    def add_agent_proposal_atomically(self, proposal: AgentProposal, event: DomainEvent) -> None:
        with self._lock:
            if proposal.fingerprint in self._proposal_fingerprints:
                return
            self._agent_proposals[proposal.proposal_id] = deepcopy(proposal)
            self._proposal_fingerprints[proposal.fingerprint] = proposal.proposal_id
            self._append_event(event)

    def quality_risk_facts(
        self,
        work_order_id: str,
        operation_sequence: int,
        equipment_id: str,
        lookback_days: int = 30,
    ) -> dict[str, Any]:
        with self._lock:
            inspected = failed = 0
            for inspection in self._inspections.values():
                if (
                    inspection.result is None
                    or (
                        inspection.work_order_id == work_order_id
                        and inspection.operation_sequence == operation_sequence
                    )
                ):
                    continue
                order = self._orders.get(inspection.work_order_id)
                operation = next(
                    (
                        item
                        for item in order.operations
                        if item.sequence == inspection.operation_sequence
                    ),
                    None,
                ) if order else None
                if operation and operation.assigned_resource_id == equipment_id:
                    inspected += 1
                    failed += int(inspection.result == "FAIL")
            cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
            alarms = sum(
                item["eventType"] == "EquipmentTelemetryRecorded"
                and item["aggregateId"] == equipment_id
                and datetime.fromisoformat(item["occurredAt"]) >= cutoff
                and bool(
                    item["payload"].get("alarmCode")
                    or item["payload"].get("state") in {"ALARM", "DOWN"}
                )
                for item in self._outbox
            )
            tool_lives = [
                resource.life_remaining_percent
                for session in self._execution_sessions.values()
                if session.work_order_id == work_order_id
                and session.operation_sequence == operation_sequence
                for resource in session.resources
                if resource.resource_type == "TOOL"
                and resource.life_remaining_percent is not None
            ]
            return {
                "historicalInspections": inspected,
                "historicalFailures": failed,
                "recentAlarmCount": alarms,
                "minimumToolLifePercent": min(tool_lives) if tool_lives else None,
            }

    def get_quality_policy(self, policy_id: str) -> QualityRiskPolicy | None:
        with self._lock:
            item = self._quality_policies.get(policy_id)
            return deepcopy(item) if item else None

    def list_quality_policies(self, limit: int = 100) -> list[QualityRiskPolicy]:
        with self._lock:
            items = sorted(
                self._quality_policies.values(),
                key=lambda item: (item.updated_at, item.version),
                reverse=True,
            )
            return deepcopy(items[:limit])

    def next_quality_policy_version(self, policy_key: str) -> int:
        with self._lock:
            versions = [
                item.version
                for item in self._quality_policies.values()
                if item.policy_key == policy_key
            ]
            return max(versions, default=0) + 1

    def resolve_quality_risk_policy(
        self, product_revision_id: str, operation_code: str, as_of: datetime
    ) -> QualityRiskPolicy | None:
        with self._lock:
            candidates = [
                item
                for item in self._quality_policies.values()
                if item.status is QualityPolicyStatus.APPROVED
                and item.effective_from is not None
                and item.effective_from <= as_of
                and item.product_revision_id in {None, product_revision_id}
                and item.operation_code in {None, operation_code}
            ]
            candidates.sort(
                key=lambda item: (
                    int(item.product_revision_id is not None)
                    + int(item.operation_code is not None),
                    item.effective_from or item.created_at,
                    item.version,
                ),
                reverse=True,
            )
            return deepcopy(candidates[0]) if candidates else None

    def add_quality_policy_atomically(
        self, policy: QualityRiskPolicy, event: DomainEvent
    ) -> None:
        with self._lock:
            if any(
                item.policy_key == policy.policy_key and item.version == policy.version
                for item in self._quality_policies.values()
            ):
                raise InvalidTransition("quality policy version already exists")
            self._quality_policies[policy.policy_id] = deepcopy(policy)
            self._append_event(event)

    def update_quality_policy_atomically(
        self,
        policy: QualityRiskPolicy,
        expected_record_version: int,
        event: DomainEvent,
    ) -> None:
        with self._lock:
            current = self._quality_policies.get(policy.policy_id)
            if current is None or current.record_version != expected_record_version:
                raise InvalidTransition("quality policy version changed")
            self._quality_policies[policy.policy_id] = deepcopy(policy)
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

    def list_manufacturing_resources(
        self,
        limit: int = 100,
        offset: int = 0,
        query: str | None = None,
        resource_type: str | None = None,
        status: str | None = None,
    ) -> list[ManufacturingResource]:
        with self._lock:
            items = sorted(
                self._manufacturing_resources.values(),
                key=lambda item: item.updated_at,
                reverse=True,
            )
            if query:
                needle = query.casefold()
                items = [
                    item
                    for item in items
                    if needle in item.resource_id.casefold() or needle in item.name.casefold()
                ]
            if resource_type:
                items = [item for item in items if item.resource_type == resource_type]
            if status:
                items = [item for item in items if item.status == status]
            return deepcopy(items[offset : offset + limit])

    def count_manufacturing_resources(
        self,
        query: str | None = None,
        resource_type: str | None = None,
        status: str | None = None,
    ) -> int:
        with self._lock:
            items = list(self._manufacturing_resources.values())
            if query:
                needle = query.casefold()
                items = [
                    item
                    for item in items
                    if needle in item.resource_id.casefold() or needle in item.name.casefold()
                ]
            if resource_type:
                items = [item for item in items if item.resource_type == resource_type]
            if status:
                items = [item for item in items if item.status == status]
            return len(items)

    def summarize_manufacturing_resources(self) -> dict[str, int]:
        with self._lock:
            result: dict[str, int] = {
                "TOTAL": len(self._manufacturing_resources),
                "SOURCE_COUNT": len(
                    {item.source_system for item in self._manufacturing_resources.values()}
                ),
            }
            for item in self._manufacturing_resources.values():
                type_key, status_key = f"TYPE:{item.resource_type}", f"STATUS:{item.status}"
                result[type_key] = result.get(type_key, 0) + 1
                result[status_key] = result.get(status_key, 0) + 1
            return result

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
            self._manufacturing_resources.update({item.key: deepcopy(item) for item in resources})
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
