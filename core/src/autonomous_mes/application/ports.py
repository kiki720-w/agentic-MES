from dataclasses import dataclass
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

    def list_work_orders(self, limit: int = 100) -> list[WorkOrder]: ...

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


class AuthorizationPolicy(Protocol):
    def require_workshop_read(self, subject_id: str, workshop_id: str) -> None: ...


class ToolAuditSink(Protocol):
    def record_tool_event(self, event: DomainEvent) -> None: ...


class EquipmentStore(Protocol):
    def get_equipment(self, equipment_id: str) -> Equipment | None: ...

    def get_equipment_by_code(self, code: str) -> Equipment | None: ...

    def list_equipment(self, limit: int = 100) -> list[Equipment]: ...

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

    def list_agent_proposals(self, limit: int = 100) -> list[AgentProposal]: ...

    def add_agent_proposal_atomically(
        self, proposal: AgentProposal, event: DomainEvent
    ) -> None: ...


class QualityStore(Protocol):
    def get_inspection(self, inspection_id: str) -> QualityInspection | None: ...
    def list_inspections(self, limit: int = 100) -> list[QualityInspection]: ...
    def add_inspection_atomically(
        self, inspection: QualityInspection, event: DomainEvent
    ) -> None: ...
    def update_inspection_atomically(
        self, inspection: QualityInspection, expected_version: int, event: DomainEvent
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
    def list_manufacturing_resources(self, limit: int = 100) -> list[ManufacturingResource]: ...
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


class MesStore(
    WorkOrderStore,
    EquipmentStore,
    AgentProposalStore,
    QualityStore,
    GenealogyStore,
    ManufacturingResourceStore,
    ConnectorSecurityStore,
    ToolAuditSink,
    Protocol,
):
    """Combined persistence port used by the current vertical slice."""
