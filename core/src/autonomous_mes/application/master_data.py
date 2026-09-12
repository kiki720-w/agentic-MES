from dataclasses import dataclass
from datetime import datetime
from typing import Any

from autonomous_mes.domain.errors import NotFound
from autonomous_mes.domain.master_data import ManufacturingResource

from .ports import ManufacturingResourceStore


@dataclass(frozen=True)
class RegisterManufacturingResourceCommand:
    correlation_id: str
    actor_id: str
    resource_type: str
    resource_id: str
    revision: str | None
    name: str
    status: str
    life_remaining_percent: float | None
    calibration_due_at: datetime | None
    source_system: str
    external_reference: str | None
    source_updated_at: datetime


@dataclass(frozen=True)
class UpdateManufacturingResourceCommand:
    correlation_id: str
    actor_id: str
    resource_type: str
    resource_id: str
    revision: str | None
    expected_version: int
    status: str
    life_remaining_percent: float | None
    calibration_due_at: datetime | None
    source_updated_at: datetime


class ManufacturingResourceApplicationService:
    def __init__(self, store: ManufacturingResourceStore) -> None:
        self._store = store

    def register(self, command: RegisterManufacturingResourceCommand) -> dict[str, Any]:
        item = ManufacturingResource.create(
            command.resource_type,
            command.resource_id,
            command.revision,
            command.name,
            command.status,
            command.life_remaining_percent,
            command.calibration_due_at,
            command.source_system,
            command.external_reference,
            command.source_updated_at,
        )
        self._store.add_manufacturing_resource_atomically(
            item, item.event(command.correlation_id, command.actor_id)
        )
        return serialize_resource(item)

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        return [serialize_resource(item) for item in self._store.list_manufacturing_resources(limit)]

    def update(self, command: UpdateManufacturingResourceCommand) -> dict[str, Any]:
        current = self._store.get_manufacturing_resource(
            command.resource_type, command.resource_id, command.revision or ""
        )
        if current is None:
            raise NotFound("manufacturing resource not found")
        changed = current.update_state(
            command.status,
            command.life_remaining_percent,
            command.calibration_due_at,
            command.source_updated_at,
            command.expected_version,
        )
        self._store.update_manufacturing_resource_atomically(
            changed,
            current.version,
            changed.updated_event(command.correlation_id, command.actor_id),
        )
        return serialize_resource(changed)


def serialize_resource(item: ManufacturingResource) -> dict[str, Any]:
    return {
        "resourceType": item.resource_type,
        "resourceId": item.resource_id,
        "revision": item.revision or None,
        "name": item.name,
        "status": item.status,
        "lifeRemainingPercent": item.life_remaining_percent,
        "calibrationDueAt": item.calibration_due_at.isoformat() if item.calibration_due_at else None,
        "sourceSystem": item.source_system,
        "externalReference": item.external_reference,
        "sourceUpdatedAt": item.source_updated_at.isoformat(),
        "version": item.version,
    }
