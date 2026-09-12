import unittest
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from autonomous_mes.application.scheduling import (
    GenerateScheduleCommand,
    IngestSchedulingSnapshotCommand,
    RegisterPlanningResourceCommand,
    SchedulingApplicationService,
    UpdatePlanningResourceCommand,
)
from autonomous_mes.application.work_orders import (
    CreateWorkOrderCommand,
    OperationSpec,
    WorkOrderApplicationService,
)
from autonomous_mes.domain.errors import InvalidTransition
from autonomous_mes.infrastructure.memory import InMemoryWorkOrderStore


class SchedulingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryWorkOrderStore()
        self.orders = WorkOrderApplicationService(self.store)
        self.scheduling = SchedulingApplicationService(self.store)

    def create_order(self, code: str, priority: int, due_days: int = 3) -> dict[str, object]:
        return self.orders.create(
            CreateWorkOrderCommand(
                str(uuid4()),
                str(uuid4()),
                code,
                f"PO-{code}",
                "WS-1",
                10,
                datetime.now(UTC) + timedelta(days=due_days),
                priority,
                f"PR-{code}",
                f"RT-{code}",
                f"BOM-{code}",
                [],
                [OperationSpec(10, "TURN", "Turning", "WC-TURN")],
            )
        )

    def register_person(self) -> None:
        self.scheduling.register_resource(
            RegisterPlanningResourceCommand(
                "P-01",
                "Operator 01",
                "PERSON",
                "WS-1",
                "WC-TURN",
                480,
                600,
                ["TURN"],
                "demo-planner",
                str(uuid4()),
            )
        )

    def generate(self, start: date | None = None) -> dict[str, object]:
        return self.scheduling.generate(
            GenerateScheduleCommand(
                "WS-1",
                start or datetime.now(UTC).date(),
                6,
                False,
                30,
                {},
                "demo-planner",
                str(uuid4()),
            )
        )

    def test_finite_capacity_schedule_prioritizes_and_reports_load(self) -> None:
        self.create_order("WO-LOW", 20)
        self.create_order("WO-HIGH", 90)
        self.register_person()

        plan = self.generate(date(2026, 9, 11))

        assignments = plan["assignments"]
        self.assertIsInstance(assignments, list)
        assert isinstance(assignments, list)
        self.assertEqual("WO-HIGH", assignments[0]["workOrderCode"])
        per_day: dict[str, float] = {}
        for item in assignments:
            day = str(item["productionDate"])
            per_day[day] = per_day.get(day, 0) + float(item["plannedWorkMinutes"])
        self.assertTrue(all(value <= 480 for value in per_day.values()))
        self.assertNotIn("2026-09-13", per_day)
        self.assertEqual(2, plan["metrics"]["scheduledOrderCount"])
        self.assertEqual(0, plan["metrics"]["shortageCount"])

    def test_plan_lifecycle_enforces_maker_checker(self) -> None:
        self.create_order("WO-GOVERNED", 90)
        self.register_person()
        plan = self.generate()
        submitted = self.scheduling.submit(
            str(plan["planId"]), int(plan["recordVersion"]), "demo-planner"
        )
        with self.assertRaisesRegex(InvalidTransition, "maker-checker"):
            self.scheduling.approve(
                str(plan["planId"]),
                int(submitted["recordVersion"]),
                "demo-planner",
                "self approval",
            )
        approved = self.scheduling.approve(
            str(plan["planId"]),
            int(submitted["recordVersion"]),
            "demo-supervisor",
            "capacity and delivery risk reviewed",
        )
        published = self.scheduling.publish(
            str(plan["planId"]), int(approved["recordVersion"]), "demo-supervisor"
        )
        withdrawn = self.scheduling.withdraw(
            str(plan["planId"]),
            int(published["recordVersion"]),
            "demo-supervisor",
            "equipment outage",
        )
        self.assertEqual("WITHDRAWN", withdrawn["status"])
        self.assertEqual(
            [
                "WorkOrderCreated",
                "PlanningResourceRegistered",
                "SchedulePlanGenerated",
                "SchedulePlanSubmitted",
                "SchedulePlanApproved",
                "SchedulePlanPublished",
                "SchedulePlanWithdrawn",
            ],
            [item["eventType"] for item in self.store.list_outbox()],
        )

    def test_planner_can_move_draft_assignment_with_capacity_guard(self) -> None:
        self.create_order("WO-MOVE", 80)
        self.register_person()
        second = self.scheduling.register_resource(
            RegisterPlanningResourceCommand(
                "P-02",
                "Operator 02",
                "PERSON",
                "WS-1",
                "WC-TURN",
                480,
                600,
                ["TURN"],
                "demo-planner",
                str(uuid4()),
            )
        )
        plan = self.generate(date(2026, 9, 14))
        assignment = plan["assignments"][0]

        moved = self.scheduling.move_assignment(
            str(plan["planId"]),
            str(assignment["assignmentId"]),
            int(plan["recordVersion"]),
            str(second["resourceId"]),
            date(2026, 9, 15),
            "balance operator workload",
            "demo-planner",
        )

        self.assertEqual(2, moved["recordVersion"])
        self.assertEqual("P-02", moved["assignments"][0]["resourceCode"])
        self.assertEqual("2026-09-15", moved["assignments"][0]["productionDate"])
        self.assertEqual("ScheduleAssignmentMoved", self.store.list_outbox()[-1]["eventType"])

    def test_missing_capacity_is_visible_as_shortage(self) -> None:
        self.create_order("WO-NO-CAPACITY", 50)
        plan = self.generate()
        self.assertEqual([], plan["assignments"])
        self.assertEqual(1, plan["metrics"]["shortageCount"])
        self.assertIn("没有可用", plan["shortages"][0]["reason"])

    def test_planning_capacity_can_be_versioned_and_edited(self) -> None:
        self.register_person()
        resource = self.scheduling.list_resources("WS-1")[0]

        updated = self.scheduling.update_resource(
            UpdatePlanningResourceCommand(
                str(resource["resourceId"]),
                int(resource["version"]),
                "Operator 01 (night shift)",
                "WC-TURN",
                420,
                540,
                ["TURN", "DEBURR"],
                False,
                "demo-planner",
                str(uuid4()),
            )
        )

        self.assertEqual(2, updated["version"])
        self.assertEqual(420, updated["dailyCapacityMinutes"])
        self.assertFalse(updated["active"])
        self.assertEqual("PlanningResourceUpdated", self.store.list_outbox()[-1]["eventType"])

    def test_order_without_route_is_not_silently_dropped(self) -> None:
        self.orders.create(
            CreateWorkOrderCommand(
                str(uuid4()),
                str(uuid4()),
                "WO-NO-ROUTE",
                "PO-WO-NO-ROUTE",
                "WS-1",
                10,
                datetime.now(UTC) + timedelta(days=3),
                50,
                "PR-NO-ROUTE",
                "RT-NO-ROUTE",
                "BOM-NO-ROUTE",
                [],
                [],
            )
        )

        plan = self.generate()

        self.assertEqual(1, plan["metrics"]["candidateOrderCount"])
        self.assertEqual(1, plan["metrics"]["shortageCount"])
        self.assertEqual("未配置工艺路线", plan["shortages"][0]["operationName"])

    def test_external_snapshot_becomes_authoritative_scheduling_input(self) -> None:
        observed = datetime.now(UTC)
        snapshot = self.scheduling.ingest_snapshot(
            IngestSchedulingSnapshotCommand(
                "factory-edge-adapter",
                "WS-1",
                "rev-100",
                observed,
                {
                    "workOrders": [
                        {
                            "externalId": "mes-order-ready",
                            "code": "WO-EXTERNAL-READY",
                            "productionOrderId": "ERP-100",
                            "quantity": 10,
                            "dueAt": (observed + timedelta(days=3)).isoformat(),
                            "priority": 90,
                            "productRevisionId": "PR-1",
                            "routingRevisionId": "RT-1",
                            "bomRevisionId": "BOM-1",
                            "status": "RELEASED",
                            "version": 7,
                            "materialReady": True,
                            "qualityHold": False,
                            "operations": [
                                {
                                    "sequence": 10,
                                    "operationCode": "TURN",
                                    "operationName": "Turning",
                                    "workCenterId": "WC-TURN",
                                    "plannedQuantity": 10,
                                    "status": "PENDING",
                                }
                            ],
                        },
                        {
                            "externalId": "mes-order-short",
                            "code": "WO-EXTERNAL-SHORT",
                            "productionOrderId": "ERP-101",
                            "quantity": 5,
                            "dueAt": (observed + timedelta(days=4)).isoformat(),
                            "priority": 80,
                            "productRevisionId": "PR-2",
                            "routingRevisionId": "RT-2",
                            "bomRevisionId": "BOM-2",
                            "status": "RELEASED",
                            "version": 3,
                            "materialReady": False,
                            "qualityHold": False,
                            "operations": [
                                {
                                    "sequence": 10,
                                    "operationCode": "TURN",
                                    "operationName": "Turning",
                                    "workCenterId": "WC-TURN",
                                    "plannedQuantity": 5,
                                    "status": "PENDING",
                                }
                            ],
                        },
                    ],
                    "resources": [
                        {
                            "externalId": "mes-cell-1",
                            "code": "CELL-EXT-1",
                            "name": "External turning cell",
                            "resourceType": "CELL",
                            "workCenterId": "WC-TURN",
                            "dailyCapacityMinutes": 480,
                            "overtimeCapacityMinutes": 600,
                            "capabilityCodes": ["TURN"],
                            "active": True,
                            "state": "RUNNING",
                        }
                    ],
                },
                "connector:key-1",
                "nonce-1",
            )
        )
        reused = self.scheduling.ingest_snapshot(
            IngestSchedulingSnapshotCommand(
                "factory-edge-adapter",
                "WS-1",
                "rev-100",
                observed,
                self.store.get_latest_scheduling_snapshot("WS-1").payload,
                "connector:key-1",
                "nonce-2",
            )
        )

        plan = self.generate(date(2026, 9, 14))

        self.assertFalse(snapshot["reused"])
        self.assertTrue(reused["reused"])
        self.assertEqual("WO-EXTERNAL-READY", plan["assignments"][0]["workOrderCode"])
        self.assertEqual("WMS 显示物料未齐套", plan["shortages"][0]["reason"])
        self.assertEqual(
            "EXTERNAL_SCHEDULING_SNAPSHOT",
            plan["generationParameters"]["inputSource"]["type"],
        )

    def test_snapshot_process_standard_drives_work_demand(self) -> None:
        observed = datetime.now(UTC)
        self.scheduling.ingest_snapshot(
            IngestSchedulingSnapshotCommand(
                "spreadsheet",
                "WS-1",
                "rev-process-standard",
                observed,
                {
                    "workOrders": [{
                        "externalId": "order-standard",
                        "code": "WO-STANDARD",
                        "productionOrderId": "PO-STANDARD",
                        "quantity": 10,
                        "dueAt": (observed + timedelta(days=5)).isoformat(),
                        "priority": 80,
                        "productRevisionId": "PART-A",
                        "routingRevisionId": "RT-A",
                        "bomRevisionId": "BOM-A",
                        "status": "RELEASED",
                        "operations": [{
                            "sequence": 10,
                            "operationCode": "TURN",
                            "operationName": "Turning",
                            "workCenterId": "WC-TURN",
                            "plannedQuantity": 10,
                            "status": "PENDING",
                            "minutesPerUnit": 12,
                            "setupMinutes": 30,
                        }],
                    }],
                    "resources": [{
                        "externalId": "cell-standard",
                        "code": "CELL-STANDARD",
                        "name": "Turning cell",
                        "resourceType": "CELL",
                        "workCenterId": "WC-TURN",
                        "dailyCapacityMinutes": 480,
                        "overtimeCapacityMinutes": 600,
                        "capabilityCodes": ["TURN"],
                        "active": True,
                        "state": "RUNNING",
                    }],
                },
                "demo-planner",
                str(uuid4()),
            )
        )

        plan = self.generate(date(2026, 9, 14))

        assignment = plan["assignments"][0]
        self.assertEqual(150, assignment["plannedWorkMinutes"])
        self.assertEqual(10, assignment["plannedQuantity"])
        self.assertEqual(12, assignment["minutesPerUnit"])
        self.assertEqual(30, assignment["setupMinutes"])
        self.assertEqual("SNAPSHOT_PROCESS_STANDARD", assignment["demandSource"])


if __name__ == "__main__":
    unittest.main()
