import unittest
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from autonomous_mes.application.scheduling import (
    RegisterPlanningResourceCommand,
    SchedulingApplicationService,
)
from autonomous_mes.application.scheduling_agent import (
    SchedulingAgent,
    SchedulingAgentCommand,
)
from autonomous_mes.application.work_orders import (
    CreateWorkOrderCommand,
    OperationSpec,
    WorkOrderApplicationService,
)
from autonomous_mes.domain.errors import Forbidden
from autonomous_mes.infrastructure.memory import InMemoryWorkOrderStore


class SchedulingAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryWorkOrderStore()
        WorkOrderApplicationService(self.store).create(
            CreateWorkOrderCommand(
                str(uuid4()),
                str(uuid4()),
                "WO-AGENT-APS",
                "PO-AGENT-APS",
                "WS-1",
                10,
                datetime.now(UTC) + timedelta(days=4),
                90,
                "PR-1",
                "RT-1",
                "BOM-1",
                [],
                [OperationSpec(10, "TURN", "Turning", "WC-TURN")],
            )
        )
        SchedulingApplicationService(self.store).register_resource(
            RegisterPlanningResourceCommand(
                "CELL-1",
                "Turning cell",
                "CELL",
                "WS-1",
                "WC-TURN",
                480,
                600,
                ["TURN"],
                "planner-1",
                str(uuid4()),
            )
        )
        self.command = SchedulingAgentCommand("WS-1", date(2026, 9, 14), 6, False, 30, {})

    def test_l3_agent_submits_deduplicated_plan_but_cannot_publish(self) -> None:
        agent = SchedulingAgent(self.store, enabled=True, auto_submit=True)

        first = agent.analyze(self.command)
        second = agent.analyze(self.command)

        self.assertEqual("SUBMITTED_FOR_APPROVAL", first["decision"])
        self.assertEqual("PENDING_APPROVAL", first["plan"]["status"])
        self.assertEqual("HUMAN_SUPERVISOR_ONLY", first["publicationAuthority"])
        self.assertEqual("REUSED_EXISTING_PROPOSAL", second["decision"])
        self.assertEqual(first["plan"]["planId"], second["plan"]["planId"])
        self.assertEqual(1, len(self.store.list_schedule_plans("WS-1", 10)))

    def test_disabled_agent_cannot_create_plan(self) -> None:
        with self.assertRaisesRegex(Forbidden, "disabled"):
            SchedulingAgent(self.store, enabled=False, auto_submit=True).analyze(self.command)


if __name__ == "__main__":
    unittest.main()
