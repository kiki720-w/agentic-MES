import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from test_vertical_slice import create_command

from autonomous_mes.application.outbox import OutboxMessage
from autonomous_mes.application.work_orders import WorkOrderApplicationService
from autonomous_mes.infrastructure.database import Base, build_session_factory
from autonomous_mes.infrastructure.models import EventOutboxRow
from autonomous_mes.infrastructure.outbox_worker import (
    PENDING,
    PROCESSING,
    PUBLISHED,
    QUARANTINED,
    SqlAlchemyOutboxWorker,
)
from autonomous_mes.infrastructure.sqlalchemy_store import SqlAlchemyWorkOrderStore


class RecordingPublisher:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[OutboxMessage] = []

    def publish(self, message: OutboxMessage) -> None:
        self.messages.append(message)
        if self.fail:
            raise RuntimeError("broker unavailable")


class OutboxWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name) / "worker.db"
        self.engine = create_engine(f"sqlite:///{path.as_posix()}")
        Base.metadata.create_all(self.engine)
        self.sessions = build_session_factory(self.engine)
        self.store = SqlAlchemyWorkOrderStore(self.sessions)
        self.service = WorkOrderApplicationService(self.store)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def _create_event(self, key: str = "worker-create") -> str:
        self.service.create(create_command(key))
        return self.store.list_outbox()[0]["eventId"]

    def test_success_marks_event_published(self):
        event_id = self._create_event()
        publisher = RecordingPublisher()
        worker = SqlAlchemyOutboxWorker(self.sessions, publisher, worker_id="worker-a")

        self.assertEqual(1, worker.run_once())

        with self.sessions() as session:
            row = session.get(EventOutboxRow, event_id)
            self.assertEqual(PUBLISHED, row.publish_status)
            self.assertIsNotNone(row.published_at)
            self.assertIsNone(row.locked_by)
        self.assertEqual([event_id], [message.event_id for message in publisher.messages])

    def test_failure_retries_then_quarantines(self):
        event_id = self._create_event()
        publisher = RecordingPublisher(fail=True)
        worker = SqlAlchemyOutboxWorker(
            self.sessions,
            publisher,
            worker_id="worker-b",
            max_attempts=2,
            base_retry_seconds=1,
        )
        now = datetime.now(UTC)

        worker.run_once(now=now)
        with self.sessions() as session:
            first = session.get(EventOutboxRow, event_id)
            self.assertEqual(PENDING, first.publish_status)
            self.assertEqual(1, first.attempts)
            self.assertIsNotNone(first.next_attempt_at)

        worker.run_once(now=now + timedelta(seconds=2))
        with self.sessions() as session:
            second = session.get(EventOutboxRow, event_id)
            self.assertEqual(QUARANTINED, second.publish_status)
            self.assertEqual(2, second.attempts)
            self.assertIn("broker unavailable", second.last_error)

    def test_recovers_stale_processing_lease(self):
        event_id = self._create_event()
        now = datetime.now(UTC)
        with self.sessions.begin() as session:
            row = session.get(EventOutboxRow, event_id)
            row.publish_status = PROCESSING
            row.locked_by = "dead-worker"
            row.locked_at = now - timedelta(minutes=10)

        worker = SqlAlchemyOutboxWorker(self.sessions, RecordingPublisher(), worker_id="worker-c")
        self.assertEqual(1, worker.recover_stale(now - timedelta(minutes=5)))
        with self.sessions() as session:
            row = session.get(EventOutboxRow, event_id)
            self.assertEqual(PENDING, row.publish_status)
            self.assertIsNone(row.locked_by)

    def test_manual_replay_requires_actor_and_reason(self):
        event_id = self._create_event()
        with self.sessions.begin() as session:
            row = session.get(EventOutboxRow, event_id)
            row.publish_status = QUARANTINED
            row.attempts = 5

        worker = SqlAlchemyOutboxWorker(self.sessions, RecordingPublisher(), worker_id="worker-d")
        with self.assertRaises(ValueError):
            worker.replay_quarantined(event_id, "", "retry")
        self.assertTrue(worker.replay_quarantined(event_id, "admin-1", "broker restored"))
        with self.sessions() as session:
            row = session.get(EventOutboxRow, event_id)
            self.assertEqual(PENDING, row.publish_status)
            self.assertEqual(0, row.attempts)


if __name__ == "__main__":
    unittest.main()
