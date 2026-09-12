from dataclasses import dataclass
from typing import Any

from autonomous_mes.domain.errors import Forbidden, NotFound
from autonomous_mes.domain.events import DomainEvent

from .genealogy import GenealogyApplicationService
from .ports import AuthorizationPolicy, MesStore, ToolAuditSink, WorkOrderStore
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


class GetProductGenealogyTool:
    name = "get_product_genealogy"
    version = "1.0"
    risk = "R1"

    def __init__(self, store: MesStore, policy: AuthorizationPolicy) -> None:
        self._store = store
        self._policy = policy
        self._query = GenealogyApplicationService(store)

    def execute(self, context: ToolContext, product_serial: str) -> dict[str, Any]:
        serial = product_serial.strip().upper()
        unit = self._store.get_product_unit(serial)
        if unit is None:
            self._audit(context, serial, "NOT_FOUND")
            raise NotFound("product unit not found")
        order = self._store.get(unit.work_order_id)
        if order is None:
            self._audit(context, serial, "NOT_FOUND")
            raise NotFound("work order not found")
        try:
            self._policy.require_workshop_read(context.subject_id, order.workshop_id)
        except Forbidden:
            self._audit(context, serial, "DENY")
            raise
        data = self._query.get(serial)
        self._audit(context, serial, "ALLOW")
        return {
            "requestId": context.request_id,
            "tool": self.name,
            "toolVersion": self.version,
            "risk": self.risk,
            "policyDecision": "ALLOW",
            "purpose": context.purpose,
            "asOf": data["createdAt"],
            "data": data,
        }

    def _audit(self, context: ToolContext, serial: str, decision: str) -> None:
        self._store.record_tool_event(
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
                    "objectType": "ProductUnit",
                    "objectId": serial,
                },
            )
        )
