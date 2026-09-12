from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from uuid import uuid4

from .errors import InvalidTransition, ValidationError
from .events import DomainEvent, utc_now


class ProposalStatus(str, Enum):
    OBSERVED = "OBSERVED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    EXECUTED = "EXECUTED"
    REJECTED = "REJECTED"


@dataclass(frozen=True)
class AgentProposal:
    proposal_id: str
    fingerprint: str
    agent_id: str
    action: str
    risk: str
    status: ProposalStatus
    work_order_id: str
    work_order_version: int
    operation_sequence: int
    equipment_id: str
    equipment_version: int
    diagnosis: str
    rationale: str
    narrative_source: str
    model_name: str | None
    approved_by: str | None
    approval_reason: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        fingerprint: str,
        action: str,
        risk: str,
        status: ProposalStatus,
        work_order_id: str,
        work_order_version: int,
        operation_sequence: int,
        equipment_id: str,
        equipment_version: int,
        diagnosis: str,
        rationale: str,
        narrative_source: str = "RULES",
        model_name: str | None = None,
        agent_id: str = "incident-response-agent-v1",
    ) -> tuple["AgentProposal", DomainEvent]:
        now = utc_now()
        proposal = cls(
            proposal_id=str(uuid4()),
            fingerprint=fingerprint,
            agent_id=agent_id,
            action=action,
            risk=risk,
            status=status,
            work_order_id=work_order_id,
            work_order_version=work_order_version,
            operation_sequence=operation_sequence,
            equipment_id=equipment_id,
            equipment_version=equipment_version,
            diagnosis=diagnosis,
            rationale=rationale,
            narrative_source=narrative_source,
            model_name=model_name,
            approved_by=None,
            approval_reason=None,
            created_at=now,
            updated_at=now,
        )
        event = DomainEvent.create(
            "AgentProposalCreated",
            "AgentProposal",
            proposal.proposal_id,
            {
                "agentId": proposal.agent_id,
                "action": action,
                "risk": risk,
                "status": status.value,
                "workOrderId": work_order_id,
                "equipmentId": equipment_id,
                "narrativeSource": narrative_source,
                "modelName": model_name,
            },
            proposal.proposal_id,
        )
        return proposal, event

    def mark_executed(self, actor_id: str, reason: str) -> tuple["AgentProposal", DomainEvent]:
        if self.status is not ProposalStatus.PENDING_APPROVAL:
            raise InvalidTransition("proposal is not awaiting approval")
        if not actor_id.strip() or not reason.strip():
            raise ValidationError("approver and approval reason are required")
        changed = replace(
            self,
            status=ProposalStatus.EXECUTED,
            approved_by=actor_id,
            approval_reason=reason,
            updated_at=utc_now(),
        )
        event = DomainEvent.create(
            "AgentProposalExecuted",
            "AgentProposal",
            self.proposal_id,
            {
                "action": self.action,
                "approvedBy": actor_id,
                "approvalReason": reason,
                "workOrderId": self.work_order_id,
            },
            self.proposal_id,
        )
        return changed, event
