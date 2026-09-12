from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from uuid import uuid4

from .errors import InvalidTransition, ValidationError
from .events import DomainEvent, utc_now


class EquipmentState(str, Enum):
    UNKNOWN = "UNKNOWN"
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    DOWN = "DOWN"
    ALARM = "ALARM"
    OFFLINE = "OFFLINE"


@dataclass(frozen=True)
class TelemetrySample:
    sample_id: str
    observed_at: datetime
    state: EquipmentState
    spindle_load_percent: float | None = None
    temperature_celsius: float | None = None
    alarm_code: str | None = None
    downtime_reason: str | None = None

    def validate(self) -> None:
        if not self.sample_id.strip() or self.observed_at.tzinfo is None:
            raise ValidationError("sample_id and timezone-aware observed_at are required")
        if self.spindle_load_percent is not None and not 0 <= self.spindle_load_percent <= 200:
            raise ValidationError("spindle load must be between 0 and 200 percent")
        if self.temperature_celsius is not None and not -50 <= self.temperature_celsius <= 300:
            raise ValidationError("temperature must be between -50 and 300 Celsius")
        if self.state in {EquipmentState.DOWN, EquipmentState.ALARM} and not (
            self.downtime_reason or self.alarm_code
        ):
            raise ValidationError("DOWN or ALARM telemetry requires a reason or alarm code")


@dataclass(frozen=True)
class Equipment:
    equipment_id: str
    code: str
    name: str
    workshop_id: str
    work_center_id: str
    protocol: str
    state: EquipmentState
    version: int
    last_seen_at: datetime | None
    spindle_load_percent: float | None
    temperature_celsius: float | None
    alarm_code: str | None
    downtime_reason: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        code: str,
        name: str,
        workshop_id: str,
        work_center_id: str,
        protocol: str,
        correlation_id: str,
    ) -> tuple["Equipment", DomainEvent]:
        if any(not value.strip() for value in (code, name, workshop_id, work_center_id, protocol)):
            raise ValidationError("equipment code, name, location and protocol are required")
        now = utc_now()
        equipment = cls(
            equipment_id=str(uuid4()),
            code=code,
            name=name,
            workshop_id=workshop_id,
            work_center_id=work_center_id,
            protocol=protocol,
            state=EquipmentState.UNKNOWN,
            version=1,
            last_seen_at=None,
            spindle_load_percent=None,
            temperature_celsius=None,
            alarm_code=None,
            downtime_reason=None,
            created_at=now,
            updated_at=now,
        )
        event = DomainEvent.create(
            "EquipmentRegistered",
            "Equipment",
            equipment.equipment_id,
            {"code": code, "workCenterId": work_center_id, "protocol": protocol},
            correlation_id,
        )
        return equipment, event

    def record(
        self, sample: TelemetrySample, expected_version: int, correlation_id: str
    ) -> tuple["Equipment", DomainEvent]:
        sample.validate()
        if expected_version != self.version:
            raise InvalidTransition("equipment version changed; reload before telemetry ingestion")
        if self.last_seen_at is not None and sample.observed_at < self.last_seen_at:
            raise InvalidTransition(
                "telemetry observed_at is older than the current equipment state"
            )
        changed = replace(
            self,
            state=sample.state,
            version=self.version + 1,
            last_seen_at=sample.observed_at,
            spindle_load_percent=sample.spindle_load_percent,
            temperature_celsius=sample.temperature_celsius,
            alarm_code=sample.alarm_code,
            downtime_reason=sample.downtime_reason,
            updated_at=utc_now(),
        )
        event = DomainEvent.create(
            "EquipmentTelemetryRecorded",
            "Equipment",
            self.equipment_id,
            {
                "sampleId": sample.sample_id,
                "state": sample.state.value,
                "observedAt": sample.observed_at.isoformat(),
                "spindleLoadPercent": sample.spindle_load_percent,
                "temperatureCelsius": sample.temperature_celsius,
                "alarmCode": sample.alarm_code,
                "downtimeReason": sample.downtime_reason,
            },
            correlation_id,
        )
        return changed, event
