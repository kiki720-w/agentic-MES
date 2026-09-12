from dataclasses import dataclass, replace
from datetime import datetime

from .errors import InvalidTransition, ValidationError
from .events import DomainEvent, utc_now

RESOURCE_TYPES = {"TOOL", "FIXTURE", "NC_PROGRAM", "GAUGE"}


@dataclass(frozen=True)
class ManufacturingResource:
    resource_type: str
    resource_id: str
    revision: str
    name: str
    status: str
    life_remaining_percent: float | None
    calibration_due_at: datetime | None
    source_system: str
    external_reference: str | None
    source_updated_at: datetime
    version: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        resource_type: str,
        resource_id: str,
        revision: str | None,
        name: str,
        status: str,
        life_remaining_percent: float | None,
        calibration_due_at: datetime | None,
        source_system: str,
        external_reference: str | None,
        source_updated_at: datetime,
    ) -> "ManufacturingResource":
        kind = resource_type.strip().upper()
        identifier = resource_id.strip().upper()
        normalized_revision = (revision or "").strip().upper()
        state = status.strip().upper()
        source = source_system.strip().upper()
        if kind not in RESOURCE_TYPES or not identifier or not name.strip() or not source:
            raise ValidationError("valid resource type, id, name and source system are required")
        if kind == "NC_PROGRAM" and not normalized_revision:
            raise ValidationError("NC program revision is required")
        if kind == "TOOL" and (
            life_remaining_percent is None or not 0 <= life_remaining_percent <= 100
        ):
            raise ValidationError("tool life remaining must be between 0 and 100")
        if kind == "GAUGE" and calibration_due_at is None:
            raise ValidationError("gauge calibration due time is required")
        now = utc_now()
        return cls(
            kind,
            identifier,
            normalized_revision,
            name.strip(),
            state,
            life_remaining_percent,
            calibration_due_at,
            source,
            external_reference.strip() if external_reference else None,
            source_updated_at,
            1,
            now,
            now,
        )

    @property
    def key(self) -> str:
        return f"{self.resource_type}:{self.resource_id}:{self.revision}"

    def event(self, correlation_id: str, actor_id: str) -> DomainEvent:
        return DomainEvent.create(
            "ManufacturingResourceRegistered",
            "ManufacturingResource",
            self.key,
            {
                "resourceType": self.resource_type,
                "resourceId": self.resource_id,
                "revision": self.revision or None,
                "status": self.status,
                "sourceSystem": self.source_system,
                "actorId": actor_id,
            },
            correlation_id,
        )

    def update_state(
        self,
        status: str,
        life_remaining_percent: float | None,
        calibration_due_at: datetime | None,
        source_updated_at: datetime,
        expected_version: int,
    ) -> "ManufacturingResource":
        if expected_version != self.version:
            raise InvalidTransition("manufacturing resource version changed")
        if source_updated_at < self.source_updated_at:
            raise InvalidTransition("stale source update cannot overwrite newer master data")
        state = status.strip().upper()
        if self.resource_type == "TOOL" and (
            life_remaining_percent is None or not 0 <= life_remaining_percent <= 100
        ):
            raise ValidationError("tool life remaining must be between 0 and 100")
        if self.resource_type == "GAUGE" and calibration_due_at is None:
            raise ValidationError("gauge calibration due time is required")
        return replace(
            self,
            status=state,
            life_remaining_percent=life_remaining_percent,
            calibration_due_at=calibration_due_at,
            source_updated_at=source_updated_at,
            version=self.version + 1,
            updated_at=utc_now(),
        )

    def updated_event(self, correlation_id: str, actor_id: str) -> DomainEvent:
        event = self.event(correlation_id, actor_id)
        return replace(event, event_type="ManufacturingResourceUpdated")
