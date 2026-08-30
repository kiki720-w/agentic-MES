import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from autonomous_mes.application.agent_tools import GetWorkOrderTool, ToolContext
from autonomous_mes.application.work_orders import (
    CreateWorkOrderCommand,
    ReleaseWorkOrderCommand,
    WorkOrderApplicationService,
)
from autonomous_mes.domain.errors import Forbidden, InvalidTransition, ValidationError
from autonomous_mes.infrastructure.memory import InMemoryWorkOrderStore, ScopedReadPolicy


def create_command(key="create-1"):
    return CreateWorkOrderCommand(
        idempotency_key=key,
        correlation_id="corr-1",
        human_code="WO-SHAFT-001",
        production_order_id="PO-SHAFT-001",
        workshop_id="WS-MACH-01",
        quantity=10,
        due_at=datetime.now(timezone.utc) + timedelta(days=7),
        priority=80,
        product_revision_id="PR-SHAFT-A",
        routing_revision_id="RT-SHAFT-A",
        bom_revision_id="BOM-SHAFT-A",
        drawing_revision_ids=["DWG-SHAFT-A"],
    )


class VerticalSliceTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryWorkOrderStore()
        self.service = WorkOrderApplicationService(self.store)

    def test_create_writes_order_and_outbox_atomically(self):
        created = self.service.create(create_command())
        self.assertEqual("DRAFT", created["status"])
        self.assertEqual(1, created["version"])
        outbox = self.store.list_outbox()
        self.assertEqual(1, len(outbox))
        self.assertEqual("WorkOrderCreated", outbox[0]["eventType"])
        self.assertEqual(created["workOrderId"], outbox[0]["aggregateId"])

    def test_create_is_idempotent(self):
        first = self.service.create(create_command())
        second = self.service.create(create_command())
        self.assertEqual(first["workOrderId"], second["workOrderId"])
        self.assertEqual(1, len(self.store.list_outbox()))

    def test_release_freezes_revisions_and_writes_event(self):
        created = self.service.create(create_command())
        released = self.service.release(
            ReleaseWorkOrderCommand(
                idempotency_key="release-1",
                correlation_id="corr-2",
                work_order_id=created["workOrderId"],
                expected_version=1,
                actor_id="planner-1",
            )
        )
        self.assertEqual("RELEASED", released["status"])
        self.assertEqual(2, released["version"])
        self.assertEqual("RT-SHAFT-A", released["revisions"]["routingRevisionId"])
        self.assertEqual(
            ["WorkOrderCreated", "WorkOrderReleased"],
            [item["eventType"] for item in self.store.list_outbox()],
        )

    def test_release_rejects_stale_version(self):
        created = self.service.create(create_command())
        with self.assertRaises(InvalidTransition):
            self.service.release(
                ReleaseWorkOrderCommand(
                    idempotency_key="release-stale",
                    correlation_id="corr-2",
                    work_order_id=created["workOrderId"],
                    expected_version=0,
                    actor_id="planner-1",
                )
            )
        self.assertEqual(1, len(self.store.list_outbox()))

    def test_invalid_quantity_never_writes_event(self):
        command = create_command()
        invalid = CreateWorkOrderCommand(**{**command.__dict__, "quantity": 0})
        with self.assertRaises(ValidationError):
            self.service.create(invalid)
        self.assertEqual([], self.store.list_outbox())

    def test_agent_tool_allows_scoped_read(self):
        created = self.service.create(create_command())
        tool = GetWorkOrderTool(
            self.store,
            ScopedReadPolicy({"planner-1": {"WS-MACH-01"}}),
            self.store,
        )
        result = tool.execute(
            ToolContext("req-1", "agent-readonly", "planner-1", "explain status"),
            created["workOrderId"],
        )
        self.assertEqual("ALLOW", result["policyDecision"])
        self.assertEqual("DRAFT", result["data"]["status"])
        audit = self.store.list_outbox()[-1]
        self.assertEqual("AgentToolCallRecorded", audit["eventType"])
        self.assertEqual("ALLOW", audit["payload"]["policyDecision"])

    def test_agent_tool_denies_out_of_scope_read(self):
        created = self.service.create(create_command())
        tool = GetWorkOrderTool(
            self.store,
            ScopedReadPolicy({"user-other": {"WS-OTHER"}}),
            self.store,
        )
        with self.assertRaises(Forbidden):
            tool.execute(
                ToolContext("req-2", "agent-readonly", "user-other", "explain status"),
                created["workOrderId"],
            )
        audit = self.store.list_outbox()[-1]
        self.assertEqual("AgentToolCallRecorded", audit["eventType"])
        self.assertEqual("DENY", audit["payload"]["policyDecision"])


if __name__ == "__main__":
    unittest.main()
