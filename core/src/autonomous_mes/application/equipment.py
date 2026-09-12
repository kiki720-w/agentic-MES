from dataclasses import dataclass
from datetime import datetime
from typing import Any

from autonomous_mes.application.ports import EquipmentStore
from autonomous_mes.domain.equipment import Equipment, EquipmentState, TelemetrySample
from autonomous_mes.domain.errors import IdempotencyConflict, NotFound, ValidationError


@dataclass(frozen=True)
class RegisterEquipmentCommand:
    correlation_id: str
    code: str
    name: str
    workshop_id: str
    work_center_id: str
    protocol: str


@dataclass(frozen=True)
class RecordTelemetryCommand:
    correlation_id: str
    equipment_id: str
    expected_version: int
    sample_id: str
    observed_at: datetime
    state: str
    spindle_load_percent: float | None = None
    temperature_celsius: float | None = None
    alarm_code: str | None = None
    downtime_reason: str | None = None


class EquipmentApplicationService:
    def __init__(self, store: EquipmentStore) -> None:
        self._store = store

    def register(self, command: RegisterEquipmentCommand) -> dict[str, Any]:
        existing = self._store.get_equipment_by_code(command.code)
        if existing is not None:
            raise IdempotencyConflict("equipment code already exists")
        equipment, event = Equipment.create(
            command.code,
            command.name,
            command.workshop_id,
            command.work_center_id,
            command.protocol,
            command.correlation_id,
        )
        self._store.add_equipment_atomically(equipment, event)
        return _serialize(equipment)

    def record(self, command: RecordTelemetryCommand) -> dict[str, Any]:
        current = self._store.get_equipment(command.equipment_id)
        if current is None:
            raise NotFound("equipment not found")
        if self._store.telemetry_sample_exists(command.sample_id):
            return _serialize(current)
        try:
            state = EquipmentState(command.state)
        except ValueError as exc:
            raise ValidationError("unsupported equipment state") from exc
        sample = TelemetrySample(
            command.sample_id,
            command.observed_at,
            state,
            command.spindle_load_percent,
            command.temperature_celsius,
            command.alarm_code,
            command.downtime_reason,
        )
        changed, event = current.record(sample, command.expected_version, command.correlation_id)
        self._store.record_telemetry_atomically(changed, current.version, sample, event)
        return _serialize(changed)

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        if limit < 1 or limit > 500:
            raise ValidationError("limit must be between 1 and 500")
        return [_serialize(item) for item in self._store.list_equipment(limit)]


def _serialize(item: Equipment) -> dict[str, Any]:
    return {
        "equipmentId": item.equipment_id,
        "code": item.code,
        "name": item.name,
        "workshopId": item.workshop_id,
        "workCenterId": item.work_center_id,
        "protocol": item.protocol,
        "state": item.state.value,
        "version": item.version,
        "lastSeenAt": item.last_seen_at.isoformat() if item.last_seen_at else None,
        "spindleLoadPercent": item.spindle_load_percent,
        "temperatureCelsius": item.temperature_celsius,
        "alarmCode": item.alarm_code,
        "downtimeReason": item.downtime_reason,
    }
