from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

from autonomous_mes.domain.events import DomainEvent
from autonomous_mes.domain.work_order import WorkOrder


@dataclass(frozen=True)
class IdempotentResult:
    operation: str
    resource_id: str
    resource_version: int


class WorkOrderStore(Protocol):
    def get(self, work_order_id: str) -> Optional[WorkOrder]: ...

    def get_by_human_code(self, human_code: str) -> Optional[WorkOrder]: ...

    def save_atomically(
        self,
        work_order: WorkOrder,
        expected_stored_version: Optional[int],
        events: List[DomainEvent],
        idempotency_key: str,
        idempotent_result: IdempotentResult,
    ) -> IdempotentResult: ...

    def get_idempotent_result(self, idempotency_key: str) -> Optional[IdempotentResult]: ...

    def list_outbox(self) -> List[Dict[str, Any]]: ...


class AuthorizationPolicy(Protocol):
    def require_workshop_read(self, subject_id: str, workshop_id: str) -> None: ...


class ToolAuditSink(Protocol):
    def record_tool_event(self, event: DomainEvent) -> None: ...
