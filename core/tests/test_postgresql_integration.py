import os
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from autonomous_mes.application.genealogy import (
    GenealogyApplicationService,
    RegisterProductUnitCommand,
)
from autonomous_mes.application.outbox import OutboxMessage
from autonomous_mes.application.work_orders import (
    CreateWorkOrderCommand,
    OperationSpec,
    ReleaseWorkOrderCommand,
    WorkOrderApplicationService,
)
from autonomous_mes.domain.errors import IdempotencyConflict
from autonomous_mes.infrastructure.database import build_session_factory
from autonomous_mes.infrastructure.models import (
    EventOutboxRow,
    WorkOrderOperationRow,
    WorkOrderRow,
)
from autonomous_mes.infrastructure.outbox_worker import SqlAlchemyOutboxWorker
from autonomous_mes.infrastructure.sqlalchemy_store import SqlAlchemyWorkOrderStore

DATABASE_URL = os.environ.get("AUTONOMOUS_MES_POSTGRES_TEST_URL")


class ThreadSafePublisher:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.event_ids: list[str] = []

    def publish(self, message: OutboxMessage) -> None:
        with self._lock:
            self.event_ids.append(message.event_id)


@unittest.skipUnless(DATABASE_URL, "set AUTONOMOUS_MES_POSTGRES_TEST_URL to run")
class PostgreSqlIntegrationTests(unittest.TestCase):
    engine: Engine
    sessions: sessionmaker[Session]

    @classmethod
    def setUpClass(cls) -> None:
        assert DATABASE_URL is not None
        cls.engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        cls.sessions = build_session_factory(cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()

    def _command(self, suffix: str, human_code: str | None = None) -> CreateWorkOrderCommand:
        return CreateWorkOrderCommand(
            idempotency_key=f"pg-test-{suffix}",
            correlation_id=f"pg-correlation-{suffix}",
            human_code=human_code or f"WO-PG-TEST-{suffix}",
            production_order_id=f"PO-PG-TEST-{suffix}",
            workshop_id="WS-MACH-01",
            quantity=10,
            due_at=datetime.now(UTC) + timedelta(days=7),
            priority=80,
            product_revision_id="PR-SHAFT-A",
            routing_revision_id="RT-SHAFT-A",
            bom_revision_id="BOM-SHAFT-A",
            drawing_revision_ids=["DWG-SHAFT-A"],
            operations=[OperationSpec(10, "TURN", "数控车削", "WC-LATHE-01")],
        )

    def test_normalized_operation_rows_follow_aggregate_updates(self) -> None:
        store = SqlAlchemyWorkOrderStore(self.sessions)
        service = WorkOrderApplicationService(store)
        suffix = uuid4().hex
        created = service.create(self._command(suffix))

        with self.sessions() as session:
            operation = session.get(
                WorkOrderOperationRow,
                (str(created["workOrderId"]), 10),
            )
            self.assertIsNotNone(operation)
            assert operation is not None
            self.assertEqual("PENDING", operation.status)

        service.release(
            ReleaseWorkOrderCommand(
                f"pg-release-operation-{suffix}",
                f"pg-rel-op-{suffix}",
                str(created["workOrderId"]),
                int(created["version"]),
                "planner-1",
            )
        )
        with self.sessions() as session:
            operation = session.get(
                WorkOrderOperationRow,
                (str(created["workOrderId"]), 10),
            )
            self.assertIsNotNone(operation)
            assert operation is not None
            self.assertEqual("PENDING", operation.status)

        projection = store.inspect_operation_projection()
        self.assertEqual(0, projection["mismatchCount"])
        self.assertEqual(0, projection["extraCount"])

    def test_unique_constraint_rolls_back_order_event_and_idempotency(self) -> None:
        store = SqlAlchemyWorkOrderStore(self.sessions)
        service = WorkOrderApplicationService(store)
        suffix = uuid4().hex
        human_code = f"WO-PG-ROLLBACK-{suffix}"
        service.create(self._command(suffix, human_code))

        with self.sessions() as session:
            orders_before = session.scalar(select(func.count()).select_from(WorkOrderRow))
            events_before = session.scalar(select(func.count()).select_from(EventOutboxRow))

        with self.assertRaises(IdempotencyConflict):
            service.create(self._command(f"{suffix}-duplicate", human_code))

        with self.sessions() as session:
            orders_after = session.scalar(select(func.count()).select_from(WorkOrderRow))
            events_after = session.scalar(select(func.count()).select_from(EventOutboxRow))
        self.assertEqual(orders_before, orders_after)
        self.assertEqual(events_before, events_after)

    def test_two_workers_do_not_publish_the_same_claimed_event(self) -> None:
        store = SqlAlchemyWorkOrderStore(self.sessions)
        service = WorkOrderApplicationService(store)
        for _ in range(8):
            suffix = uuid4().hex
            service.create(self._command(suffix))

        publisher = ThreadSafePublisher()
        workers = [
            SqlAlchemyOutboxWorker(self.sessions, publisher, worker_id="pg-worker-a"),
            SqlAlchemyOutboxWorker(self.sessions, publisher, worker_id="pg-worker-b"),
        ]
        with ThreadPoolExecutor(max_workers=2) as executor:
            claimed = list(executor.map(lambda worker: worker.run_once(batch_size=5), workers))

        self.assertEqual(sum(claimed), len(publisher.event_ids))
        self.assertEqual(len(publisher.event_ids), len(set(publisher.event_ids)))

    def test_product_unit_and_links_commit_in_one_postgresql_transaction(self) -> None:
        store = SqlAlchemyWorkOrderStore(self.sessions)
        orders = WorkOrderApplicationService(store)
        suffix = uuid4().hex
        created = orders.create(self._command(suffix))
        released = orders.release(
            ReleaseWorkOrderCommand(
                f"pg-release-{suffix}",
                f"pg-genealogy-{suffix}",
                str(created["workOrderId"]),
                int(created["version"]),
                "planner-1",
            )
        )
        serial = f"SN-PG-{suffix}"

        registered = GenealogyApplicationService(store).register(
            RegisterProductUnitCommand(
                f"pg-serial-{suffix}", serial, str(released["workOrderId"]), "operator-1"
            )
        )

        self.assertEqual(serial.upper(), registered["productSerial"])
        self.assertGreaterEqual(len(registered["links"]), 4)


if __name__ == "__main__":
    unittest.main()
