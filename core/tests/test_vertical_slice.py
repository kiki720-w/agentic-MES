import os
import sys
import unittest
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from autonomous_mes.application.agent_tools import GetWorkOrderTool, ToolContext
from autonomous_mes.application.work_orders import (
    CreateWorkOrderCommand,
    OperationCommand,
    OperationSpec,
    ReleaseWorkOrderCommand,
    WorkOrderApplicationService,
)
from autonomous_mes.domain.errors import (
    Forbidden,
    IdempotencyConflict,
    InvalidTransition,
    ValidationError,
)
from autonomous_mes.infrastructure.memory import InMemoryWorkOrderStore, ScopedReadPolicy


def create_command(key="create-1"):
    return CreateWorkOrderCommand(
        idempotency_key=key,
        correlation_id="corr-1",
        human_code="WO-SHAFT-001",
        production_order_id="PO-SHAFT-001",
        workshop_id="WS-MACH-01",
        quantity=10,
        due_at=datetime.now(UTC) + timedelta(days=7),
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
        command = create_command()
        first = self.service.create(command)
        second = self.service.create(command)
        self.assertEqual(first["workOrderId"], second["workOrderId"])
        self.assertEqual(1, len(self.store.list_outbox()))

    def test_idempotency_key_rejects_changed_payload(self):
        self.service.create(create_command())
        changed = create_command()
        changed = CreateWorkOrderCommand(**{**changed.__dict__, "quantity": 11})
        with self.assertRaises(IdempotencyConflict):
            self.service.create(changed)

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

    def test_operation_route_executes_dispatch_start_report_complete(self):
        command = create_command("operation-create")
        command = CreateWorkOrderCommand(
            **{
                **command.__dict__,
                "operations": [
                    OperationSpec(10, "TURN", "车削", "WC-LATHE-01"),
                    OperationSpec(20, "GRIND", "外圆磨", "WC-GRIND-01"),
                ],
            }
        )
        created = self.service.create(command)
        current = self.service.release(
            ReleaseWorkOrderCommand(
                "operation-release", "corr-release", created["workOrderId"], 1, "planner-1"
            )
        )

        actions = [
            ("dispatch", {"resource_id": "LATHE-01"}),
            ("start", {}),
            ("report", {"good_quantity": 9, "scrap_quantity": 1}),
            ("complete", {}),
        ]
        for index, (action, detail) in enumerate(actions):
            current = self.service.execute_operation(
                OperationCommand(
                    idempotency_key=f"operation-{action}",
                    correlation_id=f"corr-{action}",
                    work_order_id=created["workOrderId"],
                    sequence=10,
                    expected_version=current["version"],
                    actor_id="operator-1",
                    action=action,
                    **detail,
                )
            )
            self.assertEqual(3 + index, current["version"])

        self.assertEqual("RELEASED", current["status"])
        self.assertEqual("COMPLETED", current["operations"][0]["status"])
        self.assertEqual(9, current["operations"][0]["goodQuantity"])
        self.assertEqual(1, current["operations"][0]["scrapQuantity"])

        for action, detail in [
            ("dispatch", {"resource_id": "GRIND-01"}),
            ("start", {}),
            ("report", {"good_quantity": 10}),
            ("complete", {}),
        ]:
            current = self.service.execute_operation(
                OperationCommand(
                    idempotency_key=f"operation-second-{action}",
                    correlation_id=f"corr-second-{action}",
                    work_order_id=created["workOrderId"],
                    sequence=20,
                    expected_version=current["version"],
                    actor_id="operator-1",
                    action=action,
                    **detail,
                )
            )
        self.assertEqual("COMPLETED", current["status"])
        self.assertEqual("COMPLETED", current["operations"][1]["status"])

    def test_cannot_dispatch_second_operation_before_first_completes(self):
        command = create_command("sequence-create")
        command = CreateWorkOrderCommand(
            **{
                **command.__dict__,
                "operations": [
                    OperationSpec(10, "TURN", "车削", "WC-LATHE-01"),
                    OperationSpec(20, "GRIND", "外圆磨", "WC-GRIND-01"),
                ],
            }
        )
        created = self.service.create(command)
        released = self.service.release(
            ReleaseWorkOrderCommand(
                "sequence-release", "corr-release", created["workOrderId"], 1, "planner-1"
            )
        )
        with self.assertRaises(InvalidTransition):
            self.service.execute_operation(
                OperationCommand(
                    "sequence-dispatch",
                    "corr-dispatch",
                    created["workOrderId"],
                    20,
                    released["version"],
                    "operator-1",
                    "dispatch",
                    resource_id="GRIND-01",
                )
            )


if __name__ == "__main__":
    unittest.main()
