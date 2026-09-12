from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from uuid import uuid4

from .errors import InvalidTransition, ValidationError
from .events import DomainEvent, utc_now


class InspectionStatus(str, Enum):
    OPEN = "OPEN"
    PASSED = "PASSED"
    QUARANTINED = "QUARANTINED"
    REWORK_APPROVED = "REWORK_APPROVED"


@dataclass(frozen=True)
class QualityInspection:
    inspection_id: str
    work_order_id: str
    operation_sequence: int
    sample_size: int
    status: InspectionStatus
    version: int
    result: str | None
    defect_code: str | None
    notes: str | None
    rework_route: list[str]
    created_at: datetime
    updated_at: datetime
    gauge_id: str | None = None
    calibration_due_at: datetime | None = None
    measurement_recorded_at: datetime | None = None

    @classmethod
    def create(
        cls, work_order_id: str, operation_sequence: int, sample_size: int
    ) -> "QualityInspection":
        if not work_order_id.strip() or operation_sequence < 10 or sample_size <= 0:
            raise ValidationError("work order, operation and positive sample size are required")
        now = utc_now()
        return cls(
            str(uuid4()),
            work_order_id,
            operation_sequence,
            sample_size,
            InspectionStatus.OPEN,
            1,
            None,
            None,
            None,
            [],
            now,
            now,
        )

    def record(
        self,
        passed: bool,
        defect_code: str | None,
        notes: str | None,
        expected_version: int,
        gauge_id: str,
        calibration_due_at: datetime,
        measurement_recorded_at: datetime,
    ) -> "QualityInspection":
        if expected_version != self.version or self.status is not InspectionStatus.OPEN:
            raise InvalidTransition("inspection is no longer open or version changed")
        if not passed and not (defect_code or "").strip():
            raise ValidationError("defect code is required for a failed inspection")
        if not gauge_id.strip():
            raise ValidationError("gauge id is required")
        if calibration_due_at < measurement_recorded_at:
            raise ValidationError("gauge calibration expired before measurement")
        return replace(
            self,
            status=InspectionStatus.PASSED if passed else InspectionStatus.QUARANTINED,
            version=self.version + 1,
            result="PASS" if passed else "FAIL",
            defect_code=defect_code,
            notes=notes,
            gauge_id=gauge_id.strip().upper(),
            calibration_due_at=calibration_due_at,
            measurement_recorded_at=measurement_recorded_at,
            updated_at=utc_now(),
        )

    def approve_rework(self, route: list[str], expected_version: int) -> "QualityInspection":
        if expected_version != self.version or self.status is not InspectionStatus.QUARANTINED:
            raise InvalidTransition("only the current quarantined inspection can enter rework")
        if not route or any(not item.strip() for item in route):
            raise ValidationError("at least one valid rework operation is required")
        return replace(
            self,
            status=InspectionStatus.REWORK_APPROVED,
            version=self.version + 1,
            rework_route=list(route),
            updated_at=utc_now(),
        )

    def event(
        self,
        event_type: str,
        correlation_id: str,
        actor_id: str,
        source_proposal_id: str | None = None,
    ) -> DomainEvent:
        return DomainEvent.create(
            event_type=event_type,
            aggregate_type="QualityInspection",
            aggregate_id=self.inspection_id,
            correlation_id=correlation_id,
            payload={
                "workOrderId": self.work_order_id,
                "operationSequence": self.operation_sequence,
                "status": self.status.value,
                "result": self.result,
                "defectCode": self.defect_code,
                "reworkRoute": self.rework_route,
                "gaugeId": self.gauge_id,
                "calibrationDueAt": (
                    self.calibration_due_at.isoformat() if self.calibration_due_at else None
                ),
                "actorId": actor_id,
                **({"sourceProposalId": source_proposal_id} if source_proposal_id else {}),
            },
        )
