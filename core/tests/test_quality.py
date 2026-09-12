from datetime import UTC, datetime
from unittest import TestCase

from autonomous_mes.application.agent_runtime import IncidentResponseAgent
from autonomous_mes.application.equipment import (
    EquipmentApplicationService,
    RegisterEquipmentCommand,
)
from autonomous_mes.application.quality import CreateInspectionCommand, QualityApplicationService
from autonomous_mes.application.work_orders import (
    CreateWorkOrderCommand,
    OperationCommand,
    OperationSpec,
    ReleaseWorkOrderCommand,
    WorkOrderApplicationService,
)
from autonomous_mes.infrastructure.memory import InMemoryWorkOrderStore


class QualityWorkflowTests(TestCase):
    def setUp(self) -> None:
        self.store = InMemoryWorkOrderStore()
        self.orders = WorkOrderApplicationService(self.store)
        self.quality = QualityApplicationService(self.store, self.store)
        self.equipment = EquipmentApplicationService(self.store)

    def completed_order(self) -> dict[str, object]:
        equipment = self.equipment.register(
            RegisterEquipmentCommand(
                "q-equipment", "LATHE-01", "精车设备", "WS-MACH-01", "WC-01", "SIMULATED"
            )
        )
        order = self.orders.create(
            CreateWorkOrderCommand(
                "q-create",
                "c",
                "WO-Q-1",
                "PO-Q",
                "WS-MACH-01",
                1,
                datetime(2027, 1, 1, tzinfo=UTC),
                50,
                "P1",
                "R1",
                "B1",
                [],
                [OperationSpec(10, "OP10", "精车", "WC-01")],
            )
        )
        order = self.orders.release(
            ReleaseWorkOrderCommand(
                "q-release", "c", str(order["workOrderId"]), int(order["version"]), "planner"
            )
        )
        for action, extra in [
            ("dispatch", {"resource_id": str(equipment["equipmentId"])}),
            ("start", {}),
            ("report", {"good_quantity": 1}),
            ("complete", {}),
        ]:
            order = self.orders.execute_operation(
                OperationCommand(
                    f"q-{action}",
                    "c",
                    str(order["workOrderId"]),
                    10,
                    int(order["version"]),
                    "operator",
                    action,
                    **extra,
                )
            )
        return order

    def test_agent_creates_idempotent_quality_recommendation_draft(self) -> None:
        order = self.completed_order()
        agent = IncidentResponseAgent(self.store)

        first = agent.recommend_quality_inspection(str(order["workOrderId"]), 10)
        second = agent.recommend_quality_inspection(str(order["workOrderId"]), 10)

        self.assertEqual(first["proposalId"], second["proposalId"])
        self.assertEqual("CREATE_QUALITY_INSPECTION", first["action"])
        self.assertEqual("OBSERVED", first["status"])
        self.assertEqual("quality-recommendation-agent-v1", first["agentId"])
        self.assertEqual("COMPLETED", self.orders.get(str(order["workOrderId"]))["status"])

    def test_failed_inspection_is_quarantined_and_rework_requires_approval(self) -> None:
        order = self.completed_order()
        inspection = self.quality.create(
            CreateInspectionCommand("q", str(order["workOrderId"]), 10, 1, "inspector")
        )
        inspection = self.quality.record(
            str(inspection["inspectionId"]), False, "DIM-001", "轴径超差", 1, "inspector", "q2"
        )
        assert inspection["status"] == "QUARANTINED"
        inspection = self.quality.approve_rework(
            str(inspection["inspectionId"]),
            ["OP10-REWORK", "OP90-REINSPECT"],
            2,
            "quality-lead",
            "q3",
        )
        assert inspection["status"] == "REWORK_APPROVED"
        assert [x["eventType"] for x in self.store.list_outbox()][-2:] == [
            "ProductQuarantined",
            "ReworkRouteApproved",
        ]
