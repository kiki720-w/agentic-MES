from hashlib import sha256
from typing import Any

from autonomous_mes.application.equipment import EquipmentApplicationService
from autonomous_mes.application.ports import MesStore
from autonomous_mes.application.work_orders import OperationCommand, WorkOrderApplicationService
from autonomous_mes.domain.agent import AgentProposal, ProposalStatus
from autonomous_mes.domain.errors import Forbidden, InvalidTransition, NotFound, ValidationError

from .model_gateway import (
    DiagnosticFacts,
    DiagnosticModel,
    DiagnosticNarrative,
    ModelGatewayError,
)


class RuleBasedNarrator:
    def explain(self, facts: DiagnosticFacts) -> DiagnosticNarrative:
        if facts.healthy:
            return DiagnosticNarrative(
                "设备已恢复健康，但工序仍因历史联锁保持暂停",
                "建议主管核对现场安全条件后批准复工",
                "RULES",
            )
        return DiagnosticNarrative(
            f"设备仍处于{facts.equipment_state}，禁止恢复生产",
            "保持工序暂停，等待维修或操作人员处理设备异常",
            "RULES",
        )


class FallbackNarrator:
    def __init__(self, primary: DiagnosticModel, fallback: DiagnosticModel | None = None) -> None:
        self._primary = primary
        self._fallback = fallback or RuleBasedNarrator()

    def explain(self, facts: DiagnosticFacts) -> DiagnosticNarrative:
        try:
            return self._primary.explain(facts)
        except ModelGatewayError:
            return self._fallback.explain(facts)


class IncidentResponseAgent:
    def __init__(
        self,
        store: MesStore,
        narrator: DiagnosticModel | None = None,
        allow_production_execution: bool = False,
        approver_ids: set[str] | None = None,
    ) -> None:
        self._store = store
        self._work_orders = WorkOrderApplicationService(store)
        self._equipment = EquipmentApplicationService(store)
        self._narrator = narrator or RuleBasedNarrator()
        self._allow_production_execution = allow_production_execution
        self._approver_ids = approver_ids or set()

    def analyze(self) -> list[dict[str, Any]]:
        proposals: list[dict[str, Any]] = []
        for work_order in self._work_orders.list(500):
            if work_order["status"] != "SUSPENDED":
                continue
            operation = next(
                (item for item in work_order["operations"] if item["status"] == "SUSPENDED"),
                None,
            )
            if operation is None or not operation["assignedResourceId"]:
                continue
            equipment = self._equipment.get(str(operation["assignedResourceId"]))
            healthy = equipment["state"] in {"IDLE", "RUNNING"}
            action = "RESUME_OPERATION" if healthy else "HOLD_AND_INSPECT"
            risk = "R2" if healthy else "R0"
            status = ProposalStatus.PENDING_APPROVAL if healthy else ProposalStatus.OBSERVED
            narrative = self._narrator.explain(
                DiagnosticFacts(
                    work_order_code=str(work_order["humanCode"]),
                    work_order_status=str(work_order["status"]),
                    operation_sequence=int(operation["sequence"]),
                    operation_name=str(operation["operationName"]),
                    equipment_code=str(equipment["code"]),
                    equipment_state=str(equipment["state"]),
                    alarm_code=equipment["alarmCode"],
                    downtime_reason=equipment["downtimeReason"],
                    healthy=healthy,
                    observed_at=equipment["lastSeenAt"],
                )
            )
            fingerprint = sha256(
                (
                    f"{work_order['workOrderId']}:{work_order['version']}:"
                    f"{equipment['equipmentId']}:{equipment['version']}:{action}"
                ).encode()
            ).hexdigest()
            proposal = self._store.get_agent_proposal_by_fingerprint(fingerprint)
            if proposal is None:
                proposal, event = AgentProposal.create(
                    fingerprint,
                    action,
                    risk,
                    status,
                    str(work_order["workOrderId"]),
                    int(work_order["version"]),
                    int(operation["sequence"]),
                    str(equipment["equipmentId"]),
                    int(equipment["version"]),
                    narrative.diagnosis,
                    narrative.recommendation,
                    narrative.source,
                    narrative.model,
                )
                self._store.add_agent_proposal_atomically(proposal, event)
            proposals.append(_serialize(proposal))
        return proposals

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        if limit < 1 or limit > 500:
            raise ValidationError("limit must be between 1 and 500")
        return [_serialize(item) for item in self._store.list_agent_proposals(limit)]

    def approve(self, proposal_id: str, actor_id: str, reason: str) -> dict[str, Any]:
        if not self._allow_production_execution:
            raise Forbidden("Agent L3 production execution is disabled in the Stage-1 L2 baseline")
        if actor_id not in self._approver_ids:
            raise Forbidden("actor is not authorized to approve Agent L3 execution")
        proposal = self._store.get_agent_proposal(proposal_id)
        if proposal is None:
            raise NotFound("agent proposal not found")
        if proposal.action != "RESUME_OPERATION":
            raise InvalidTransition("proposal does not contain an executable action")
        equipment = self._equipment.get(proposal.equipment_id)
        if equipment["state"] not in {"IDLE", "RUNNING"}:
            raise InvalidTransition("equipment is not healthy; approval cannot be executed")
        self._work_orders.execute_operation(
            OperationCommand(
                idempotency_key=f"agent-proposal:{proposal.proposal_id}",
                correlation_id=proposal.proposal_id,
                work_order_id=proposal.work_order_id,
                sequence=proposal.operation_sequence,
                expected_version=proposal.work_order_version,
                actor_id=actor_id,
                action="resume",
            )
        )
        executed, event = proposal.mark_executed(actor_id, reason)
        self._store.update_agent_proposal_atomically(
            executed, ProposalStatus.PENDING_APPROVAL, event
        )
        return _serialize(executed)

    def recommend_quality_inspection(
        self,
        work_order_id: str,
        operation_sequence: int,
        *,
        source_event_id: str | None = None,
        source_correlation_id: str | None = None,
    ) -> dict[str, Any]:
        work_order = self._work_orders.get(work_order_id)
        operation = next(
            (
                item
                for item in work_order["operations"]
                if item["sequence"] == operation_sequence
            ),
            None,
        )
        if operation is None or operation["status"] != "COMPLETED":
            raise ValidationError("quality recommendation requires a completed operation")
        if not operation["assignedResourceId"]:
            raise ValidationError("completed operation has no traceable equipment")
        if self._store.get_inspection_for_operation(work_order_id, operation_sequence):
            raise InvalidTransition("quality inspection already exists for this operation")
        equipment = self._equipment.get(str(operation["assignedResourceId"]))
        proposal = self._store.get_quality_recommendation(work_order_id, operation_sequence)
        if proposal is None:
            fingerprint = sha256(
                f"quality:{work_order_id}:{operation_sequence}".encode()
            ).hexdigest()
            proposal = self._store.get_agent_proposal_by_fingerprint(fingerprint)
        if proposal is None:
            proposal, event = AgentProposal.create(
                fingerprint,
                "CREATE_QUALITY_INSPECTION",
                "R2",
                ProposalStatus.OBSERVED,
                work_order_id,
                int(work_order["version"]),
                operation_sequence,
                str(equipment["equipmentId"]),
                int(equipment["version"]),
                "工序已完工且尚无检验任务，建议由检验员创建质量检验。",
                "该记录仅为建议草稿，不会创建检验、隔离产品或批准返工。",
                agent_id="quality-recommendation-agent-v1",
                correlation_id=source_correlation_id,
                causation_id=source_event_id,
            )
            self._store.add_agent_proposal_atomically(proposal, event)
            proposal = self._store.get_agent_proposal_by_fingerprint(fingerprint) or proposal
        return _serialize(proposal)


def _serialize(item: AgentProposal) -> dict[str, Any]:
    return {
        "proposalId": item.proposal_id,
        "agentId": item.agent_id,
        "action": item.action,
        "risk": item.risk,
        "status": item.status.value,
        "workOrderId": item.work_order_id,
        "workOrderVersion": item.work_order_version,
        "operationSequence": item.operation_sequence,
        "equipmentId": item.equipment_id,
        "equipmentVersion": item.equipment_version,
        "diagnosis": item.diagnosis,
        "rationale": item.rationale,
        "narrativeSource": item.narrative_source,
        "modelName": item.model_name,
        "approvedBy": item.approved_by,
        "approvalReason": item.approval_reason,
        "createdAt": item.created_at.isoformat(),
        "updatedAt": item.updated_at.isoformat(),
    }
