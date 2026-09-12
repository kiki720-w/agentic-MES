from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class OutboxMessage:
    event_id: str
    event_type: str
    schema_version: str
    aggregate_type: str
    aggregate_id: str
    occurred_at: datetime
    correlation_id: str | None
    causation_id: str | None
    payload: dict[str, Any]
    attempts: int


class EventPublisher(Protocol):
    def publish(self, message: OutboxMessage) -> None: ...
