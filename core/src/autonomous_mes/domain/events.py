from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class DomainEvent:
    event_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    occurred_at: datetime
    payload: dict[str, Any]
    correlation_id: str | None = None
    causation_id: str | None = None
    schema_version: str = "1.0"

    @classmethod
    def create(
        cls,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> "DomainEvent":
        return cls(
            event_id=str(uuid4()),
            event_type=event_type,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            occurred_at=utc_now(),
            payload=payload,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
