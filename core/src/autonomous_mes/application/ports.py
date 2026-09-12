from dataclasses import dataclass
from typing import Any, Protocol

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


class MesStore(WorkOrderStore, ToolAuditSink, Protocol):
    """Combined persistence port used by the current vertical slice."""
