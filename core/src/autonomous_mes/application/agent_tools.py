from dataclasses import dataclass
from typing import Any

from autonomous_mes.domain.errors import Forbidden, NotFound
from autonomous_mes.domain.events import DomainEvent

from .ports import AuthorizationPolicy, ToolAuditSink, WorkOrderStore
from .work_orders import WorkOrderApplicationService


@dataclass(frozen=True)
class ToolContext:
    request_id: str
    agent_id: str
    subject_id: str
    purpose: str


class GetWorkOrderTool:
    name = "get_work_order"
    version = "1.0"
    risk = "R0"

    def __init__(
        self,
        store: WorkOrderStore,
        policy: AuthorizationPolicy,
        audit_sink: ToolAuditSink,
    ) -> None:
        self._store = store
        self._policy = policy
        self._audit_sink = audit_sink
        self._query = WorkOrderApplicationService(store)

    def execute(self, context: ToolContext, work_order_id: str) -> dict[str, Any]:
        item = self._store.get(work_order_id)
        if item is None:
            self._audit(context, work_order_id, "NOT_FOUND")
            raise NotFound("work order not found")
        try:
            self._policy.require_workshop_read(context.subject_id, item.workshop_id)
        except Forbidden:
            self._audit(context, work_order_id, "DENY")
            raise
        data = self._query.get(work_order_id)
        self._audit(context, work_order_id, "ALLOW")
        return {
            "requestId": context.request_id,
            "tool": self.name,
            "toolVersion": self.version,
            "policyDecision": "ALLOW",
            "purpose": context.purpose,
            "data": data,
        }

    def _audit(self, context: ToolContext, work_order_id: str, decision: str) -> None:
        self._audit_sink.record_tool_event(
            DomainEvent.create(
                event_type="AgentToolCallRecorded",
                aggregate_type="AgentAction",
                aggregate_id=context.request_id,
                correlation_id=context.request_id,
                payload={
                    "agentId": context.agent_id,
                    "subjectId": context.subject_id,
                    "purpose": context.purpose,
                    "tool": self.name,
                    "toolVersion": self.version,
                    "risk": self.risk,
                    "policyDecision": decision,
                    "objectType": "WorkOrder",
                    "objectId": work_order_id,
                },
            )
        )
