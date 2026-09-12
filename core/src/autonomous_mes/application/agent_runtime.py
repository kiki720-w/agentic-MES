from hashlib import sha256
from typing import Any, Protocol

from autonomous_mes.application.equipment import EquipmentApplicationService
from autonomous_mes.application.ports import MesStore
from autonomous_mes.application.work_orders import OperationCommand, WorkOrderApplicationService
from autonomous_mes.domain.agent import AgentProposal, ProposalStatus
from autonomous_mes.domain.errors import InvalidTransition, NotFound, ValidationError


class AgentNarrator(Protocol):
    def explain(self, equipment_state: str, healthy: bool) -> tuple[str, str]: ...


class RuleBasedNarrator:
    def explain(self, equipment_state: str, healthy: bool) -> tuple[str, str]:
        if healthy:
            return (
                "设备已恢复健康，但工序仍因历史联锁保持暂停",
                "建议主管核对现场安全条件后批准复工",
            )
        return (
            f"设备仍处于{equipment_state}，禁止恢复生产",
            "保持工序暂停，等待维修或操作人员处理设备异常",
        )


class IncidentResponseAgent:
    def __init__(self, store: MesStore, narrator: AgentNarrator | None = None) -> None:
        self._store = store
        self._work_orders = WorkOrderApplicationService(store)
        self._equipment = EquipmentApplicationService(store)
        self._narrator = narrator or RuleBasedNarrator()

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
            diagnosis, rationale = self._narrator.explain(str(equipment["state"]), healthy)
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
                    diagnosis,
                    rationale,
                )
                self._store.add_agent_proposal_atomically(proposal, event)
            proposals.append(_serialize(proposal))
        return proposals

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        if limit < 1 or limit > 500:
            raise ValidationError("limit must be between 1 and 500")
        return [_serialize(item) for item in self._store.list_agent_proposals(limit)]

    def approve(self, proposal_id: str, actor_id: str, reason: str) -> dict[str, Any]:
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
        "approvedBy": item.approved_by,
        "approvalReason": item.approval_reason,
        "createdAt": item.created_at.isoformat(),
        "updatedAt": item.updated_at.isoformat(),
    }
