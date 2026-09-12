from dataclasses import dataclass
from datetime import datetime
from typing import Any

from autonomous_mes.domain.agent import AgentProposal, ProposalStatus
from autonomous_mes.domain.errors import InvalidTransition, NotFound, ValidationError
from autonomous_mes.domain.quality import QualityInspection

from .ports import ManufacturingResourceStore, QualityStore, WorkOrderStore


@dataclass(frozen=True)
class CreateInspectionCommand:
    correlation_id: str
    work_order_id: str
    operation_sequence: int
    sample_size: int
    actor_id: str


@dataclass(frozen=True)
class ConfirmQualityRecommendationCommand:
    proposal_id: str
    sample_size: int
    actor_id: str
    reason: str
    correlation_id: str


class QualityApplicationService:
    def __init__(
        self,
        store: QualityStore,
        orders: WorkOrderStore,
        resources: ManufacturingResourceStore | None = None,
    ) -> None:
        self._store, self._orders = store, orders
        self._resources = resources

    def create(self, command: CreateInspectionCommand) -> dict[str, Any]:
        order = self._orders.get(command.work_order_id)
        if order is None:
            raise NotFound("work order not found")
        operation = next(
            (x for x in order.operations if x.sequence == command.operation_sequence), None
        )
        if operation is None or operation.status.value != "COMPLETED":
            raise ValidationError("inspection requires a completed operation")
        if self._store.get_inspection_for_operation(
            command.work_order_id, command.operation_sequence
        ):
            raise InvalidTransition("inspection already exists for this operation")
        item = QualityInspection.create(
            command.work_order_id, command.operation_sequence, command.sample_size
        )
        self._store.add_inspection_atomically(
            item, item.event("QualityInspectionCreated", command.correlation_id, command.actor_id)
        )
        return _serialize(item)

    def confirm_recommendation(
        self, command: ConfirmQualityRecommendationCommand
    ) -> dict[str, Any]:
        proposal = self._store.get_agent_proposal(command.proposal_id)
        if proposal is None:
            raise NotFound("agent proposal not found")
        if proposal.action != "CREATE_QUALITY_INSPECTION":
            raise InvalidTransition("proposal is not a quality inspection recommendation")
        existing = self._store.get_inspection_for_operation(
            proposal.work_order_id, proposal.operation_sequence
        )
        if proposal.status is ProposalStatus.EXECUTED:
            if existing is None:
                raise InvalidTransition("executed quality proposal has no inspection")
            return {
                "proposal": _serialize_proposal_confirmation(proposal),
                "inspection": _serialize(existing),
            }
        if proposal.status is not ProposalStatus.OBSERVED:
            raise InvalidTransition("quality recommendation is no longer actionable")
        if existing is not None:
            raise InvalidTransition("inspection already exists for this operation")
        order = self._orders.get(proposal.work_order_id)
        if order is None:
            raise NotFound("work order not found")
        operation = next(
            (item for item in order.operations if item.sequence == proposal.operation_sequence),
            None,
        )
        if operation is None or operation.status.value != "COMPLETED":
            raise InvalidTransition("operation is no longer eligible for quality inspection")
        inspection = QualityInspection.create(
            proposal.work_order_id,
            proposal.operation_sequence,
            command.sample_size,
        )
        changed_proposal, proposal_event = proposal.accept_quality_recommendation(
            command.actor_id,
            command.reason,
            inspection.inspection_id,
            command.correlation_id,
        )
        inspection_event = inspection.event(
            "QualityInspectionCreated",
            command.correlation_id,
            command.actor_id,
            proposal.proposal_id,
        )
        self._store.create_inspection_from_proposal_atomically(
            inspection,
            changed_proposal,
            ProposalStatus.OBSERVED,
            [inspection_event, proposal_event],
        )
        return {
            "proposal": _serialize_proposal_confirmation(changed_proposal),
            "inspection": _serialize(inspection),
        }

    def record(
        self,
        inspection_id: str,
        passed: bool,
        defect_code: str | None,
        notes: str | None,
        expected_version: int,
        actor_id: str,
        correlation_id: str,
        gauge_id: str,
        measurement_recorded_at: datetime,
    ) -> dict[str, Any]:
        current = self._require(inspection_id)
        gauge = (
            self._resources.get_manufacturing_resource("GAUGE", gauge_id)
            if self._resources
            else None
        )
        if gauge is None or gauge.calibration_due_at is None or gauge.status != "AVAILABLE":
            raise ValidationError("available gauge master record is required")
        changed = current.record(
            passed,
            defect_code,
            notes,
            expected_version,
            gauge_id,
            gauge.calibration_due_at,
            measurement_recorded_at,
        )
        self._store.update_inspection_atomically(
            changed,
            current.version,
            changed.event(
                "QualityInspectionPassed" if passed else "ProductQuarantined",
                correlation_id,
                actor_id,
            ),
        )
        return _serialize(changed)

    def approve_rework(
        self,
        inspection_id: str,
        route: list[str],
        expected_version: int,
        actor_id: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        current = self._require(inspection_id)
        changed = current.approve_rework(route, expected_version)
        self._store.update_inspection_atomically(
            changed, current.version, changed.event("ReworkRouteApproved", correlation_id, actor_id)
        )
        return _serialize(changed)

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValidationError("limit must be between 1 and 500")
        return [_serialize(x) for x in self._store.list_inspections(limit)]

    def list_page(
        self,
        limit: int = 30,
        offset: int = 0,
        query: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise ValidationError("limit must be between 1 and 100")
        if not 0 <= offset <= 1_000_000:
            raise ValidationError("offset must be between 0 and 1000000")
        normalized_query = query.strip() if query else None
        normalized_status = status.strip().upper() if status else None
        items = self._store.list_inspections(limit, offset, normalized_query, normalized_status)
        return {
            "items": [_serialize(item) for item in items],
            "count": len(items),
            "total": self._store.count_inspections(normalized_query, normalized_status),
            "limit": limit,
            "offset": offset,
        }

    def summary(self) -> dict[str, Any]:
        statuses = self._store.summarize_inspections()
        return {
            "total": sum(statuses.values()),
            "open": statuses.get("OPEN", 0),
            "quarantined": statuses.get("QUARANTINED", 0),
            "statusCounts": statuses,
        }

    def list_eligible_page(
        self,
        limit: int = 30,
        offset: int = 0,
        query: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise ValidationError("limit must be between 1 and 100")
        if not 0 <= offset <= 1_000_000:
            raise ValidationError("offset must be between 0 and 1000000")
        normalized_query = query.strip() if query else None
        items = self._store.list_eligible_quality_operations(
            limit,
            offset,
            normalized_query,
        )
        return {
            "items": items,
            "count": len(items),
            "total": self._store.count_eligible_quality_operations(normalized_query),
            "limit": limit,
            "offset": offset,
        }

    def _require(self, inspection_id: str) -> QualityInspection:
        item = self._store.get_inspection(inspection_id)
        if item is None:
            raise NotFound("quality inspection not found")
        return item


def _serialize(x: QualityInspection) -> dict[str, Any]:
    return {
        "inspectionId": x.inspection_id,
        "workOrderId": x.work_order_id,
        "operationSequence": x.operation_sequence,
        "sampleSize": x.sample_size,
        "status": x.status.value,
        "version": x.version,
        "result": x.result,
        "defectCode": x.defect_code,
        "notes": x.notes,
        "reworkRoute": x.rework_route,
        "gaugeId": x.gauge_id,
        "calibrationDueAt": x.calibration_due_at.isoformat() if x.calibration_due_at else None,
        "measurementRecordedAt": (
            x.measurement_recorded_at.isoformat() if x.measurement_recorded_at else None
        ),
        "createdAt": x.created_at.isoformat(),
        "updatedAt": x.updated_at.isoformat(),
    }


def _serialize_proposal_confirmation(item: AgentProposal) -> dict[str, Any]:
    return {
        "proposalId": item.proposal_id,
        "action": item.action,
        "risk": item.risk,
        "status": item.status.value,
        "approvedBy": item.approved_by,
        "approvalReason": item.approval_reason,
    }
