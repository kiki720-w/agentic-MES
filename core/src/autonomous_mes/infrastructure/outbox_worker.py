import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from autonomous_mes.application.outbox import EventPublisher, OutboxMessage

from .models import EventOutboxRow

PENDING = "PENDING"
PROCESSING = "PROCESSING"
PUBLISHED = "PUBLISHED"
QUARANTINED = "QUARANTINED"


class SqlAlchemyOutboxWorker:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        publisher: EventPublisher,
        *,
        worker_id: str,
        max_attempts: int = 5,
        base_retry_seconds: int = 2,
    ) -> None:
        if max_attempts < 1 or base_retry_seconds < 1:
            raise ValueError("retry settings must be positive")
        self._sessions = sessions
        self._publisher = publisher
        self._worker_id = worker_id
        self._max_attempts = max_attempts
        self._base_retry_seconds = base_retry_seconds

    def run_once(self, batch_size: int = 50, now: datetime | None = None) -> int:
        if batch_size < 1 or batch_size > 500:
            raise ValueError("batch_size must be between 1 and 500")
        current_time = now or datetime.now(UTC)
        messages = self._claim(batch_size, current_time)
        for message in messages:
            try:
                self._publisher.publish(message)
            except Exception as exc:  # noqa: BLE001 - isolate publisher failure per message
                self._mark_failed(message, str(exc), current_time)
            else:
                self._mark_published(message.event_id, current_time)
        return len(messages)

    def recover_stale(self, older_than: datetime) -> int:
        with self._sessions.begin() as session:
            result = session.execute(
                update(EventOutboxRow)
                .where(
                    EventOutboxRow.publish_status == PROCESSING,
                    EventOutboxRow.locked_at < older_than,
                )
                .values(
                    publish_status=PENDING,
                    locked_at=None,
                    locked_by=None,
                    last_error="worker lease expired",
                )
            )
            return int(getattr(result, "rowcount", 0))

    def replay_quarantined(self, event_id: str, actor_id: str, reason: str) -> bool:
        if not actor_id.strip() or not reason.strip():
            raise ValueError("actor_id and reason are required")
        with self._sessions.begin() as session:
            result = session.execute(
                update(EventOutboxRow)
                .where(
                    EventOutboxRow.event_id == event_id,
                    EventOutboxRow.publish_status == QUARANTINED,
                )
                .values(
                    publish_status=PENDING,
                    attempts=0,
                    next_attempt_at=None,
                    locked_at=None,
                    locked_by=None,
                    last_error=f"manual replay by {actor_id}: {reason}"[:2000],
                )
            )
            return getattr(result, "rowcount", 0) == 1

    def _claim(self, batch_size: int, now: datetime) -> list[OutboxMessage]:
        with self._sessions.begin() as session:
            rows = session.scalars(
                select(EventOutboxRow)
                .where(
                    EventOutboxRow.publish_status == PENDING,
                    or_(
                        EventOutboxRow.next_attempt_at.is_(None),
                        EventOutboxRow.next_attempt_at <= now,
                    ),
                )
                .order_by(EventOutboxRow.occurred_at, EventOutboxRow.event_id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            ).all()
            for row in rows:
                row.publish_status = PROCESSING
                row.locked_at = now
                row.locked_by = self._worker_id
            return [_to_message(row) for row in rows]

    def _mark_published(self, event_id: str, now: datetime) -> None:
        with self._sessions.begin() as session:
            session.execute(
                update(EventOutboxRow)
                .where(
                    EventOutboxRow.event_id == event_id,
                    EventOutboxRow.publish_status == PROCESSING,
                    EventOutboxRow.locked_by == self._worker_id,
                )
                .values(
                    publish_status=PUBLISHED,
                    published_at=now,
                    locked_at=None,
                    locked_by=None,
                    last_error=None,
                )
            )

    def _mark_failed(self, message: OutboxMessage, error: str, now: datetime) -> None:
        attempts = message.attempts + 1
        quarantined = attempts >= self._max_attempts
        delay = self._base_retry_seconds * (2 ** (attempts - 1))
        with self._sessions.begin() as session:
            session.execute(
                update(EventOutboxRow)
                .where(
                    EventOutboxRow.event_id == message.event_id,
                    EventOutboxRow.publish_status == PROCESSING,
                    EventOutboxRow.locked_by == self._worker_id,
                )
                .values(
                    publish_status=QUARANTINED if quarantined else PENDING,
                    attempts=attempts,
                    next_attempt_at=None if quarantined else now + timedelta(seconds=delay),
                    locked_at=None,
                    locked_by=None,
                    last_error=error[:2000],
                )
            )


class LoggingEventPublisher:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("autonomous_mes.outbox")

    def publish(self, message: OutboxMessage) -> None:
        self._logger.info(
            "manufacturing_event_published",
            extra={
                "event_id": message.event_id,
                "event_type": message.event_type,
                "aggregate_id": message.aggregate_id,
            },
        )


def _to_message(row: EventOutboxRow) -> OutboxMessage:
    return OutboxMessage(
        event_id=row.event_id,
        event_type=row.event_type,
        schema_version=row.schema_version,
        aggregate_type=row.aggregate_type,
        aggregate_id=row.aggregate_id,
        occurred_at=row.occurred_at,
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        payload=row.payload,
        attempts=row.attempts,
    )
