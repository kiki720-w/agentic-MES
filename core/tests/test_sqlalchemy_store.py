import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, func, select
from test_vertical_slice import create_command

from autonomous_mes.application.agent_tools import GetWorkOrderTool, ToolContext
from autonomous_mes.application.genealogy import (
    GenealogyApplicationService,
    RegisterProductUnitCommand,
)
from autonomous_mes.application.work_orders import (
    ReleaseWorkOrderCommand,
    WorkOrderApplicationService,
)
from autonomous_mes.infrastructure.database import Base, build_session_factory
from autonomous_mes.infrastructure.memory import ScopedReadPolicy
from autonomous_mes.infrastructure.models import (
    AgentToolAuditRow,
    EventOutboxRow,
    GenealogyLinkRow,
    ProductUnitRow,
    WorkOrderRow,
)
from autonomous_mes.infrastructure.sqlalchemy_store import SqlAlchemyWorkOrderStore


class SqlAlchemyStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name) / "store.db"
        self.engine = create_engine(f"sqlite:///{path.as_posix()}")
        Base.metadata.create_all(self.engine)
        self.sessions = build_session_factory(self.engine)
        self.store = SqlAlchemyWorkOrderStore(self.sessions)
        self.service = WorkOrderApplicationService(self.store)

    def tearDown(self):
        self.engine.dispose()
        self.temp.cleanup()

    def test_create_and_release_persist_with_outbox(self):
        created = self.service.create(create_command("db-create"))
        self.service.release(
            ReleaseWorkOrderCommand(
                idempotency_key="db-release",
                correlation_id="db-correlation",
                work_order_id=created["workOrderId"],
                expected_version=1,
                actor_id="planner-1",
            )
        )
        with self.sessions() as session:
            order_count = session.scalar(select(func.count()).select_from(WorkOrderRow))
            event_count = session.scalar(select(func.count()).select_from(EventOutboxRow))
            persisted = session.get(WorkOrderRow, created["workOrderId"])
        self.assertEqual(1, order_count)
        self.assertEqual(2, event_count)
        self.assertEqual("RELEASED", persisted.status)
        self.assertEqual(2, persisted.version)

    def test_agent_decision_persists_to_outbox_and_audit(self):
        created = self.service.create(create_command("db-agent-create"))
        tool = GetWorkOrderTool(
            self.store,
            ScopedReadPolicy({"planner-1": {"WS-MACH-01"}}),
            self.store,
        )
        tool.execute(
            ToolContext("db-request", "agent-readonly", "planner-1", "explain"),
            created["workOrderId"],
        )
        with self.sessions() as session:
            audit_count = session.scalar(select(func.count()).select_from(AgentToolAuditRow))
            event_count = session.scalar(select(func.count()).select_from(EventOutboxRow))
        self.assertEqual(1, audit_count)
        self.assertEqual(2, event_count)

    def test_product_serial_persists_version_genealogy(self):
        created = self.service.create(create_command("db-genealogy-create"))
        released = self.service.release(
            ReleaseWorkOrderCommand(
                "db-genealogy-release",
                "db-genealogy",
                str(created["workOrderId"]),
                int(created["version"]),
                "planner-1",
            )
        )
        result = GenealogyApplicationService(self.store).register(
            RegisterProductUnitCommand(
                "serial-register", "SN-SHAFT-0001", str(released["workOrderId"]), "operator-1"
            )
        )

        with self.sessions() as session:
            unit_count = session.scalar(select(func.count()).select_from(ProductUnitRow))
            link_count = session.scalar(select(func.count()).select_from(GenealogyLinkRow))
        self.assertEqual("SN-SHAFT-0001", result["productSerial"])
        self.assertEqual(1, unit_count)
        self.assertGreaterEqual(link_count, 4)


if __name__ == "__main__":
    unittest.main()
