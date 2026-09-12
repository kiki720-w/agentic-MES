from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from autonomous_mes.domain.errors import InvalidTransition, NotFound, ValidationError
from autonomous_mes.domain.master_data import ManufacturingResource

from .ports import ManufacturingResourceStore

ResourceList = list[ManufacturingResource]
RowList = list[dict[str, Any]]


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
        return [
            serialize_resource(item) for item in self._store.list_manufacturing_resources(limit)
        ]

    def list_page(
        self,
        limit: int = 30,
        offset: int = 0,
        query: str | None = None,
        resource_type: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise ValidationError("limit must be between 1 and 100")
        if not 0 <= offset <= 1_000_000:
            raise ValidationError("offset must be between 0 and 1000000")
        normalized_query = query.strip() if query else None
        normalized_type = resource_type.strip().upper() if resource_type else None
        normalized_status = status.strip().upper() if status else None
        items = self._store.list_manufacturing_resources(
            limit,
            offset,
            normalized_query,
            normalized_type,
            normalized_status,
        )
        return {
            "items": [serialize_resource(item) for item in items],
            "count": len(items),
            "total": self._store.count_manufacturing_resources(
                normalized_query,
                normalized_type,
                normalized_status,
            ),
            "limit": limit,
            "offset": offset,
        }

    def summary(self) -> dict[str, Any]:
        values = self._store.summarize_manufacturing_resources()
        type_counts = {key[5:]: value for key, value in values.items() if key.startswith("TYPE:")}
        status_counts = {
            key[7:]: value for key, value in values.items() if key.startswith("STATUS:")
        }
        return {
            "total": values.get("TOTAL", 0),
            "sourceCount": values.get("SOURCE_COUNT", 0),
            "typeCounts": type_counts,
            "statusCounts": status_counts,
        }

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

    def preview_csv(self, csv_text: str, source_system: str) -> dict[str, Any]:
        resources, rows, errors, digest = self._parse_csv(csv_text, source_system)
        return {
            "previewId": digest,
            "rowCount": len(rows) + len(errors),
            "validCount": len(resources),
            "errorCount": len(errors),
            "rows": rows,
            "errors": errors,
        }

    def import_csv(
        self,
        csv_text: str,
        source_system: str,
        expected_preview_id: str,
        actor_id: str,
        correlation_id: str,
    ) -> dict[str, Any]:
        resources, _, errors, digest = self._parse_csv(csv_text, source_system)
        if digest != expected_preview_id:
            raise InvalidTransition("CSV content changed after preview")
        if errors:
            raise ValidationError("CSV import contains validation errors")
        self._store.add_manufacturing_resources_atomically(
            resources,
            [item.event(correlation_id, actor_id) for item in resources],
        )
        return {"previewId": digest, "importedCount": len(resources)}

    def _parse_csv(
        self, csv_text: str, source_system: str
    ) -> tuple[ResourceList, RowList, RowList, str]:
        if len(csv_text.encode("utf-8")) > 1_000_000:
            raise ValidationError("CSV import must not exceed 1 MB")
        digest = hashlib.sha256(csv_text.encode("utf-8")).hexdigest()
        reader = csv.DictReader(io.StringIO(csv_text.lstrip("\ufeff")))
        required = {"resourceType", "resourceId", "name", "status", "sourceUpdatedAt"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValidationError("CSV template headers are missing")
        raw_rows = list(reader)
        if not 1 <= len(raw_rows) <= 500:
            raise ValidationError("CSV import must contain 1 to 500 rows")
        resources: list[ManufacturingResource] = []
        rows: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        seen: set[str] = set()
        for number, row in enumerate(raw_rows, start=2):
            try:
                item = ManufacturingResource.create(
                    row.get("resourceType", ""),
                    row.get("resourceId", ""),
                    row.get("revision") or None,
                    row.get("name", ""),
                    row.get("status", ""),
                    float(row["lifeRemainingPercent"]) if row.get("lifeRemainingPercent") else None,
                    datetime.fromisoformat(row["calibrationDueAt"])
                    if row.get("calibrationDueAt")
                    else None,
                    source_system,
                    row.get("externalReference") or None,
                    datetime.fromisoformat(row["sourceUpdatedAt"]),
                )
                if item.key in seen or self._store.get_manufacturing_resource(
                    item.resource_type, item.resource_id, item.revision
                ):
                    raise ValidationError("duplicate resource key")
                seen.add(item.key)
                resources.append(item)
                rows.append({"line": number, **serialize_resource(item)})
            except (ValueError, ValidationError) as exc:
                errors.append({"line": number, "message": str(exc)})
        return resources, rows, errors, digest


def serialize_resource(item: ManufacturingResource) -> dict[str, Any]:
    return {
        "resourceType": item.resource_type,
        "resourceId": item.resource_id,
        "revision": item.revision or None,
        "name": item.name,
        "status": item.status,
        "lifeRemainingPercent": item.life_remaining_percent,
        "calibrationDueAt": item.calibration_due_at.isoformat()
        if item.calibration_due_at
        else None,
        "sourceSystem": item.source_system,
        "externalReference": item.external_reference,
        "sourceUpdatedAt": item.source_updated_at.isoformat(),
        "version": item.version,
    }
