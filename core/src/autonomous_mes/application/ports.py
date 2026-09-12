from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from autonomous_mes.domain.agent import AgentProposal, ProposalStatus
from autonomous_mes.domain.equipment import Equipment, TelemetrySample
from autonomous_mes.domain.events import DomainEvent
from autonomous_mes.domain.genealogy import (
    ExecutionSession,
    GenealogyLink,
    MaterialConsumption,
    ProductUnit,
)
from autonomous_mes.domain.master_data import ManufacturingResource
from autonomous_mes.domain.quality import QualityInspection
from autonomous_mes.domain.quality_policy import QualityRiskPolicy
from autonomous_mes.domain.scheduling import PlanningResource, SchedulePlan
from autonomous_mes.domain.work_order import WorkOrder

if TYPE_CHECKING:
    from .connector_security import ConnectorReceipt


@dataclass(frozen=True)
class IdempotentResult:
    operation: str
    resource_id: str
    resource_version: int
    request_hash: str


class WorkOrderStore(Protocol):
    def get(self, work_order_id: str) -> WorkOrder | None: ...

    def get_by_human_code(self, human_code: str) -> WorkOrder | None: ...

    def list_work_orders(
        self,
        limit: int = 100,
        offset: int = 0,
        query: str | None = None,
        status: str | None = None,
        include_test: bool = False,
    ) -> list[WorkOrder]: ...

    def count_work_orders(
        self,
        query: str | None = None,
        status: str | None = None,
        include_test: bool = False,
    ) -> int: ...

    def summarize_work_orders(self, include_test: bool = False) -> dict[str, int]: ...

    def save_atomically(
        self,
        work_order: WorkOrder,
        expected_stored_version: int | None,
        events: list[DomainEvent],
        idempotency_key: str,
        idempotent_result: IdempotentResult,
    ) -> IdempotentResult: ...

    def get_idempotent_result(self, idempotency_key: str) -> IdempotentResult | None: ...

    def list_outbox(self) -> list[dict[str, Any]]: ...

    def list_recent_outbox(
        self,
        limit: int = 100,
        offset: int = 0,
        before_occurred_at: datetime | None = None,
        before_event_id: str | None = None,
        query: str | None = None,
        publish_status: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def count_outbox(
        self, query: str | None = None, publish_status: str | None = None
    ) -> int: ...


class AuthorizationPolicy(Protocol):
    def require_workshop_read(self, subject_id: str, workshop_id: str) -> None: ...


class ToolAuditSink(Protocol):
    def record_tool_event(self, event: DomainEvent) -> None: ...


class EquipmentStore(Protocol):
    def get_equipment(self, equipment_id: str) -> Equipment | None: ...

    def get_equipment_by_code(self, code: str) -> Equipment | None: ...

    def list_equipment(
        self,
        limit: int = 100,
        offset: int = 0,
        query: str | None = None,
        state: str | None = None,
    ) -> list[Equipment]: ...

    def count_equipment(self, query: str | None = None, state: str | None = None) -> int: ...

    def summarize_equipment(self) -> dict[str, int]: ...

    def telemetry_sample_exists(self, sample_id: str) -> bool: ...

    def add_equipment_atomically(self, equipment: Equipment, event: DomainEvent) -> None: ...

    def record_telemetry_atomically(
        self,
        equipment: Equipment,
        expected_stored_version: int,
        sample: TelemetrySample,
        event: DomainEvent,
    ) -> None: ...


class AgentProposalStore(Protocol):
    def get_agent_proposal(self, proposal_id: str) -> AgentProposal | None: ...

    def get_agent_proposal_by_fingerprint(self, fingerprint: str) -> AgentProposal | None: ...

    def get_quality_recommendation(
        self, work_order_id: str, operation_sequence: int
    ) -> AgentProposal | None: ...

    def list_agent_proposals(self, limit: int = 100) -> list[AgentProposal]: ...

    def add_agent_proposal_atomically(
        self, proposal: AgentProposal, event: DomainEvent
    ) -> None: ...

    def quality_risk_facts(
        self,
        work_order_id: str,
        operation_sequence: int,
        equipment_id: str,
        lookback_days: int = 30,
    ) -> dict[str, Any]: ...


class QualityPolicyStore(Protocol):
    def get_quality_policy(self, policy_id: str) -> QualityRiskPolicy | None: ...
    def list_quality_policies(self, limit: int = 100) -> list[QualityRiskPolicy]: ...
    def next_quality_policy_version(self, policy_key: str) -> int: ...
    def resolve_quality_risk_policy(
        self, product_revision_id: str, operation_code: str, as_of: datetime
    ) -> QualityRiskPolicy | None: ...
    def list_quality_policy_simulation_cases(
        self, policy: QualityRiskPolicy, lookback_days: int, limit: int
    ) -> list[dict[str, Any]]: ...
    def add_quality_policy_atomically(
        self, policy: QualityRiskPolicy, event: DomainEvent
    ) -> None: ...
    def update_quality_policy_atomically(
        self, policy: QualityRiskPolicy, expected_record_version: int, event: DomainEvent
    ) -> None: ...


class QualityStore(Protocol):
    def get_agent_proposal(self, proposal_id: str) -> AgentProposal | None: ...
    def get_inspection(self, inspection_id: str) -> QualityInspection | None: ...
    def get_inspection_for_operation(
        self, work_order_id: str, operation_sequence: int
    ) -> QualityInspection | None: ...
    def list_inspections(
        self,
        limit: int = 100,
        offset: int = 0,
        query: str | None = None,
        status: str | None = None,
    ) -> list[QualityInspection]: ...
    def count_inspections(self, query: str | None = None, status: str | None = None) -> int: ...
    def summarize_inspections(self) -> dict[str, int]: ...
    def list_eligible_quality_operations(
        self,
        limit: int = 30,
        offset: int = 0,
        query: str | None = None,
        workshop_id: str | None = None,
    ) -> list[dict[str, Any]]: ...
    def count_eligible_quality_operations(
        self,
        query: str | None = None,
        workshop_id: str | None = None,
    ) -> int: ...

    def inspect_operation_projection(self) -> dict[str, int]: ...
    def add_inspection_atomically(
        self, inspection: QualityInspection, event: DomainEvent
    ) -> None: ...
    def update_inspection_atomically(
        self, inspection: QualityInspection, expected_version: int, event: DomainEvent
    ) -> None: ...

    def create_inspection_from_proposal_atomically(
        self,
        inspection: QualityInspection,
        proposal: AgentProposal,
        expected_proposal_status: ProposalStatus,
        events: list[DomainEvent],
    ) -> None: ...

    def update_agent_proposal_atomically(
        self, proposal: AgentProposal, expected_status: ProposalStatus, event: DomainEvent
    ) -> None: ...


class GenealogyStore(Protocol):
    def get_product_unit(self, product_serial: str) -> ProductUnit | None: ...
    def list_genealogy_links(self, product_serial: str) -> list[GenealogyLink]: ...
    def add_product_unit_atomically(
        self, unit: ProductUnit, links: list[GenealogyLink], event: DomainEvent
    ) -> None: ...
    def list_execution_sessions(self, product_serial: str) -> list[ExecutionSession]: ...
    def list_material_consumptions(self, session_id: str) -> list[MaterialConsumption]: ...
    def add_execution_session_atomically(
        self,
        session: ExecutionSession,
        materials: list[MaterialConsumption],
        links: list[GenealogyLink],
        event: DomainEvent,
    ) -> None: ...


class ManufacturingResourceStore(Protocol):
    def get_manufacturing_resource(
        self, resource_type: str, resource_id: str, revision: str = ""
    ) -> ManufacturingResource | None: ...
    def list_manufacturing_resources(
        self,
        limit: int = 100,
        offset: int = 0,
        query: str | None = None,
        resource_type: str | None = None,
        status: str | None = None,
    ) -> list[ManufacturingResource]: ...
    def count_manufacturing_resources(
        self,
        query: str | None = None,
        resource_type: str | None = None,
        status: str | None = None,
    ) -> int: ...
    def summarize_manufacturing_resources(self) -> dict[str, int]: ...
    def add_manufacturing_resource_atomically(
        self, resource: ManufacturingResource, event: DomainEvent
    ) -> None: ...
    def update_manufacturing_resource_atomically(
        self, resource: ManufacturingResource, expected_version: int, event: DomainEvent
    ) -> None: ...
    def add_manufacturing_resources_atomically(
        self, resources: list[ManufacturingResource], events: list[DomainEvent]
    ) -> None: ...


class ConnectorSecurityStore(Protocol):
    def record_connector_receipt(self, receipt: "ConnectorReceipt") -> None: ...


class SchedulingStore(Protocol):
    def list_work_orders(
        self,
        limit: int = 100,
        offset: int = 0,
        query: str | None = None,
        status: str | None = None,
        include_test: bool = False,
    ) -> list[WorkOrder]: ...
    def list_equipment(
        self,
        limit: int = 100,
        offset: int = 0,
        query: str | None = None,
        state: str | None = None,
    ) -> list[Equipment]: ...
    def get_planning_resource(self, resource_id: str) -> PlanningResource | None: ...
    def list_planning_resources(self, workshop_id: str) -> list[PlanningResource]: ...
    def add_planning_resource_atomically(
        self, resource: PlanningResource, event: DomainEvent
    ) -> None: ...
    def get_schedule_plan(self, plan_id: str) -> SchedulePlan | None: ...
    def list_schedule_plans(self, workshop_id: str, limit: int = 30) -> list[SchedulePlan]: ...
    def add_schedule_plan_atomically(self, plan: SchedulePlan, event: DomainEvent) -> None: ...
    def update_schedule_plan_atomically(
        self, plan: SchedulePlan, expected_record_version: int, event: DomainEvent
    ) -> None: ...


class MesStore(
    WorkOrderStore,
    EquipmentStore,
    AgentProposalStore,
    QualityStore,
    GenealogyStore,
    ManufacturingResourceStore,
    ConnectorSecurityStore,
    ToolAuditSink,
    QualityPolicyStore,
    SchedulingStore,
    Protocol,
):
    """Combined persistence port used by the current vertical slice."""
