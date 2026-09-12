from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import uuid4

from .errors import InvalidTransition, ValidationError
from .events import DomainEvent, utc_now


class PlanningResourceType(str, Enum):
    PERSON = "PERSON"
    CELL = "CELL"


class SchedulePlanStatus(str, Enum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"
    WITHDRAWN = "WITHDRAWN"


@dataclass(frozen=True)
class PlanningResource:
    resource_id: str
    code: str
    name: str
    resource_type: PlanningResourceType
    workshop_id: str
    work_center_id: str
    daily_capacity_minutes: float
    overtime_capacity_minutes: float
    capability_codes: tuple[str, ...]
    active: bool
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        code: str,
        name: str,
        resource_type: str,
        workshop_id: str,
        work_center_id: str,
        daily_capacity_minutes: float,
        overtime_capacity_minutes: float,
        capability_codes: list[str],
        actor_id: str,
        correlation_id: str,
    ) -> tuple["PlanningResource", DomainEvent]:
        if any(
            not value.strip()
            for value in (code, name, resource_type, workshop_id, work_center_id, actor_id)
        ):
            raise ValidationError("planning resource identity, location and actor are required")
        try:
            kind = PlanningResourceType(resource_type.strip().upper())
        except ValueError as exc:
            raise ValidationError("planning resource type must be PERSON or CELL") from exc
        if daily_capacity_minutes <= 0:
            raise ValidationError("daily capacity must be greater than zero")
        if overtime_capacity_minutes < daily_capacity_minutes:
            raise ValidationError("overtime capacity cannot be below normal capacity")
        capabilities = tuple(
            sorted({item.strip().upper() for item in capability_codes if item.strip()})
        )
        now = utc_now()
        resource = cls(
            str(uuid4()),
            code.strip(),
            name.strip(),
            kind,
            workshop_id.strip(),
            work_center_id.strip(),
            float(daily_capacity_minutes),
            float(overtime_capacity_minutes),
            capabilities,
            True,
            1,
            now,
            now,
        )
        event = DomainEvent.create(
            "PlanningResourceRegistered",
            "PlanningResource",
            resource.resource_id,
            {
                "code": resource.code,
                "resourceType": resource.resource_type.value,
                "workshopId": resource.workshop_id,
                "workCenterId": resource.work_center_id,
                "actorId": actor_id,
            },
            correlation_id,
        )
        return resource, event


@dataclass(frozen=True)
class SchedulePlan:
    plan_id: str
    plan_number: str
    workshop_id: str
    horizon_start: date
    horizon_days: int
    use_overtime: bool
    generation_parameters: dict[str, Any]
    assignments: tuple[dict[str, Any], ...]
    shortages: tuple[dict[str, Any], ...]
    metrics: dict[str, Any]
    status: SchedulePlanStatus
    record_version: int
    created_by: str
    submitted_by: str | None
    approved_by: str | None
    approval_reason: str | None
    published_by: str | None
    withdrawn_by: str | None
    withdrawal_reason: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create_draft(
        cls,
        workshop_id: str,
        horizon_start: date,
        horizon_days: int,
        use_overtime: bool,
        generation_parameters: dict[str, Any],
        assignments: list[dict[str, Any]],
        shortages: list[dict[str, Any]],
        metrics: dict[str, Any],
        actor_id: str,
        correlation_id: str,
    ) -> tuple["SchedulePlan", DomainEvent]:
        if not workshop_id.strip() or not actor_id.strip():
            raise ValidationError("workshop and creator are required")
        if not 1 <= horizon_days <= 31:
            raise ValidationError("schedule horizon must be between 1 and 31 working days")
        now = utc_now()
        plan_id = str(uuid4())
        plan = cls(
            plan_id,
            f"APS-{now:%Y%m%d-%H%M%S}-{plan_id[:6].upper()}",
            workshop_id,
            horizon_start,
            horizon_days,
            use_overtime,
            generation_parameters,
            tuple(assignments),
            tuple(shortages),
            metrics,
            SchedulePlanStatus.DRAFT,
            1,
            actor_id,
            None,
            None,
            None,
            None,
            None,
            None,
            now,
            now,
        )
        return plan, plan._event("SchedulePlanGenerated", actor_id, correlation_id)

    def submit(
        self, expected_record_version: int, actor_id: str, correlation_id: str
    ) -> tuple["SchedulePlan", DomainEvent]:
        self._require(SchedulePlanStatus.DRAFT, expected_record_version)
        if not self.assignments:
            raise InvalidTransition("a plan without assignments cannot be submitted")
        changed = replace(
            self,
            status=SchedulePlanStatus.PENDING_APPROVAL,
            submitted_by=actor_id,
            record_version=self.record_version + 1,
            updated_at=utc_now(),
        )
        return changed, changed._event("SchedulePlanSubmitted", actor_id, correlation_id)

    def move_assignment(
        self,
        expected_record_version: int,
        assignment_id: str,
        target_resource: dict[str, str],
        production_date: date,
        reason: str,
        actor_id: str,
        correlation_id: str,
    ) -> tuple["SchedulePlan", DomainEvent]:
        self._require(SchedulePlanStatus.DRAFT, expected_record_version)
        if not reason.strip():
            raise ValidationError("schedule adjustment reason is required")
        source = next(
            (item for item in self.assignments if item["assignmentId"] == assignment_id),
            None,
        )
        if source is None:
            raise ValidationError("schedule assignment not found")
        moved = {
            **source,
            "resourceId": target_resource["resourceId"],
            "resourceCode": target_resource["resourceCode"],
            "resourceName": target_resource["resourceName"],
            "resourceType": target_resource["resourceType"],
            "productionDate": production_date.isoformat(),
            "deliveryStatus": (
                "LATE" if production_date > datetime.fromisoformat(source["dueAt"]).date() else "ON_TIME"
            ),
            "rationale": f"人工计划调整：{reason.strip()}",
        }
        assignments = tuple(
            moved if item["assignmentId"] == assignment_id else item
            for item in self.assignments
        )
        late_order_ids = {
            item["workOrderId"]
            for item in assignments
            if item["deliveryStatus"] == "LATE"
        }
        metrics = {**self.metrics, "lateOrderCount": len(late_order_ids)}
        changed = replace(
            self,
            assignments=assignments,
            metrics=metrics,
            record_version=self.record_version + 1,
            updated_at=utc_now(),
        )
        event = DomainEvent.create(
            "ScheduleAssignmentMoved",
            "SchedulePlan",
            self.plan_id,
            {
                "planNumber": self.plan_number,
                "assignmentId": assignment_id,
                "workOrderId": source["workOrderId"],
                "fromResourceId": source["resourceId"],
                "toResourceId": target_resource["resourceId"],
                "fromProductionDate": source["productionDate"],
                "toProductionDate": production_date.isoformat(),
                "reason": reason.strip(),
                "actorId": actor_id,
                "recordVersion": changed.record_version,
            },
            correlation_id,
        )
        return changed, event

    def approve(
        self,
        expected_record_version: int,
        actor_id: str,
        reason: str,
        correlation_id: str,
    ) -> tuple["SchedulePlan", DomainEvent]:
        self._require(SchedulePlanStatus.PENDING_APPROVAL, expected_record_version)
        if actor_id in {self.created_by, self.submitted_by}:
            raise InvalidTransition("maker-checker requires a different schedule approver")
        if not reason.strip():
            raise ValidationError("schedule approval reason is required")
        changed = replace(
            self,
            status=SchedulePlanStatus.APPROVED,
            approved_by=actor_id,
            approval_reason=reason.strip(),
            record_version=self.record_version + 1,
            updated_at=utc_now(),
        )
        return changed, changed._event("SchedulePlanApproved", actor_id, correlation_id)

    def publish(
        self, expected_record_version: int, actor_id: str, correlation_id: str
    ) -> tuple["SchedulePlan", DomainEvent]:
        self._require(SchedulePlanStatus.APPROVED, expected_record_version)
        changed = replace(
            self,
            status=SchedulePlanStatus.PUBLISHED,
            published_by=actor_id,
            record_version=self.record_version + 1,
            updated_at=utc_now(),
        )
        return changed, changed._event("SchedulePlanPublished", actor_id, correlation_id)

    def withdraw(
        self,
        expected_record_version: int,
        actor_id: str,
        reason: str,
        correlation_id: str,
    ) -> tuple["SchedulePlan", DomainEvent]:
        self._require(SchedulePlanStatus.PUBLISHED, expected_record_version)
        if not reason.strip():
            raise ValidationError("schedule withdrawal reason is required")
        changed = replace(
            self,
            status=SchedulePlanStatus.WITHDRAWN,
            withdrawn_by=actor_id,
            withdrawal_reason=reason.strip(),
            record_version=self.record_version + 1,
            updated_at=utc_now(),
        )
        return changed, changed._event("SchedulePlanWithdrawn", actor_id, correlation_id)

    def _require(self, status: SchedulePlanStatus, expected_record_version: int) -> None:
        if self.record_version != expected_record_version:
            raise InvalidTransition("schedule plan version changed")
        if self.status is not status:
            raise InvalidTransition(f"schedule plan must be {status.value}")

    def _event(self, event_type: str, actor_id: str, correlation_id: str) -> DomainEvent:
        if not actor_id.strip():
            raise ValidationError("schedule actor is required")
        return DomainEvent.create(
            event_type,
            "SchedulePlan",
            self.plan_id,
            {
                "planNumber": self.plan_number,
                "workshopId": self.workshop_id,
                "status": self.status.value,
                "recordVersion": self.record_version,
                "actorId": actor_id,
                "assignmentCount": len(self.assignments),
                "shortageCount": len(self.shortages),
            },
            correlation_id,
        )
