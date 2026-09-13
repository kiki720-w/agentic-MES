import unittest
from uuid import uuid4

from autonomous_mes.application.manufacturing_agent import ManufacturingAgentService
from autonomous_mes.application.model_gateway import ModelGatewayError
from autonomous_mes.application.scheduling import (
    RegisterPlanningResourceCommand,
    SchedulingApplicationService,
)
from autonomous_mes.application.work_orders import WorkOrderApplicationService
from autonomous_mes.infrastructure.memory import InMemoryWorkOrderStore


class FakeActionModel:
    def __init__(self, order: dict[str, object]) -> None:
        self.order = order

    def plan_manufacturing_action(self, instruction: str) -> dict[str, object]:
        return {"action": "CREATE_ORDER_AND_SCHEDULE", "order": self.order}


class FailingActionModel:
    def plan_manufacturing_action(self, instruction: str) -> dict[str, object]:
        raise ModelGatewayError("offline")


class ManufacturingAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryWorkOrderStore()
        self.orders = WorkOrderApplicationService(self.store)
        self.scheduling = SchedulingApplicationService(self.store)
        self.person = self.scheduling.register_resource(RegisterPlanningResourceCommand(
            "PERSON-WANG", "王师傅", "PERSON", "WS-MACH-01", "WC-LATHE-01",
            480, 600, ["TURN"], "demo-planner", str(uuid4()),
        ))

    def test_local_model_plan_creates_order_schedules_preferred_operator_and_verifies(self) -> None:
        agent = ManufacturingAgentService(self.orders, self.scheduling, FakeActionModel({
            "materialCode": "MAT-100",
            "quantity": 3,
            "dueAt": "2099-09-20",
            "operationCode": "TURN",
            "operationName": "车工",
            "workCenterId": "WC-LATHE-01",
            "minutesPerUnit": 20,
            "preferredOperator": "王师傅",
        }))

        result = agent.execute(
            "新增工单，物料编码 MAT-100，数量 3，交期 2099-09-20，车工，"
            "单件工时 20，安排王师傅来做",
            "demo-planner",
        )

        self.assertEqual("EXECUTED_AND_VERIFIED", result["status"])
        self.assertEqual("LOCAL_MODEL", result["parser"])
        self.assertEqual(1, len(result["orderAssignments"]))
        self.assertEqual(self.person["resourceId"], result["orderAssignments"][0]["resourceId"])
        self.assertEqual(1, self.orders.summary()["total"])

    def test_missing_required_fields_does_not_write(self) -> None:
        agent = ManufacturingAgentService(self.orders, self.scheduling, FakeActionModel({
            "materialCode": "MAT-100",
            "dueAt": "2099-09-20",
            "operationCode": "TURN",
            "operationName": "车工",
            "workCenterId": "WC-LATHE-01",
        }))

        result = agent.execute("新增工单但数量还没定", "demo-planner")

        self.assertEqual("NEEDS_INFORMATION", result["status"])
        self.assertIn("数量", result["missingFields"])
        self.assertEqual(0, self.orders.summary()["total"])

    def test_rule_fallback_keeps_order_entry_available_when_model_is_down(self) -> None:
        agent = ManufacturingAgentService(self.orders, self.scheduling, FailingActionModel())

        result = agent.execute(
            "新增工单，物料编码 MAT-200，数量 2，交期 2099-09-20，车工，单件工时 15",
            "demo-planner",
        )

        self.assertEqual("EXECUTED_AND_VERIFIED", result["status"])
        self.assertEqual("LOCAL_RULE_FALLBACK", result["parser"])
        self.assertEqual("MAT-200", result["order"]["revisions"]["productRevisionId"])

    def test_follow_up_fields_complete_a_pending_order_task(self) -> None:
        agent = ManufacturingAgentService(self.orders, self.scheduling, FailingActionModel())
        first = "新增工单，物料编码 MAT-300，车工"

        pending = agent.execute(first, "demo-planner")
        completed = agent.execute(
            first + "\n用户补充：数量 4，交期 2099-09-20，单件工时 12",
            "demo-planner",
        )

        self.assertEqual("NEEDS_INFORMATION", pending["status"])
        self.assertEqual("EXECUTED_AND_VERIFIED", completed["status"])
        self.assertEqual(4, completed["order"]["quantity"])

    def test_operator_name_stops_before_spoken_do_phrase(self) -> None:
        person = self.scheduling.register_resource(RegisterPlanningResourceCommand(
            "PERSON-ZHANG", "张师傅", "PERSON", "WS-MACH-01", "WC-LATHE-01",
            480, 600, ["TURN"], "demo-planner", str(uuid4()),
        ))
        agent = ManufacturingAgentService(self.orders, self.scheduling, FailingActionModel())

        result = agent.execute(
            "新增工单，物料编码 AXLE-8B，数量 2 件，交期 2099-09-20，"
            "安排张师傅做车削加工",
            "demo-planner",
        )

        self.assertEqual("EXECUTED_AND_VERIFIED", result["status"])
        self.assertEqual(person["resourceId"], result["orderAssignments"][0]["resourceId"])


if __name__ == "__main__":
    unittest.main()
