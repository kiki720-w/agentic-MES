from dataclasses import dataclass
from typing import Any, Protocol

from autonomous_mes.domain.agent import AgentProposal, ProposalStatus
from autonomous_mes.domain.equipment import Equipment, TelemetrySample
from autonomous_mes.domain.events import DomainEvent
from autonomous_mes.domain.work_order import WorkOrder


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

    def update_agent_proposal_atomically(
        self, proposal: AgentProposal, expected_status: ProposalStatus, event: DomainEvent
    ) -> None: ...


class MesStore(WorkOrderStore, EquipmentStore, AgentProposalStore, ToolAuditSink, Protocol):
    """Combined persistence port used by the current vertical slice."""
