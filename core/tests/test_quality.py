from datetime import UTC, datetime, timedelta
from unittest import TestCase
from unittest.mock import Mock

from autonomous_mes.application.agent_runtime import IncidentResponseAgent
from autonomous_mes.application.agent_tools import ListQualityCandidatesTool, ToolContext
from autonomous_mes.application.equipment import (
    EquipmentApplicationService,
    RegisterEquipmentCommand,
)
from autonomous_mes.application.event_consumers import QualityRecommendationEventPublisher
from autonomous_mes.application.master_data import (
    ManufacturingResourceApplicationService,
    RegisterManufacturingResourceCommand,
)
from autonomous_mes.application.outbox import OutboxMessage
from autonomous_mes.application.quality import CreateInspectionCommand, QualityApplicationService
from autonomous_mes.application.work_orders import (
    CreateWorkOrderCommand,
    OperationCommand,
    OperationSpec,
    ReleaseWorkOrderCommand,
    WorkOrderApplicationService,
)
from autonomous_mes.infrastructure.memory import InMemoryWorkOrderStore, ScopedReadPolicy


class QualityWorkflowTests(TestCase):
    def setUp(self) -> None:
        self.store = InMemoryWorkOrderStore()
        self.orders = WorkOrderApplicationService(self.store)
        self.quality = QualityApplicationService(self.store, self.store, self.store)
        self.equipment = EquipmentApplicationService(self.store)
        masters = ManufacturingResourceApplicationService(self.store)
        now = datetime.now(UTC)
        for index, (resource_id, due) in enumerate(
            [("GAUGE-01", now + timedelta(days=30)), ("GAUGE-EXPIRED", now - timedelta(days=1))]
        ):
            masters.register(
                RegisterManufacturingResourceCommand(
                    f"gauge-{index}",
                    "quality-admin",
                    "GAUGE",
                    resource_id,
                    None,
                    resource_id,
                    "AVAILABLE",
                    None,
                    due,
                    "MES",
                    None,
                    now,
                )
            )

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

    def completed_event(self) -> OutboxMessage:
        item = next(
            event
            for event in self.store.list_outbox()
            if event["eventType"] == "OperationCompleted"
        )
        return OutboxMessage(
            event_id=str(item["eventId"]),
            event_type=str(item["eventType"]),
            schema_version=str(item["schemaVersion"]),
            aggregate_type=str(item["aggregateType"]),
            aggregate_id=str(item["aggregateId"]),
            occurred_at=datetime.fromisoformat(str(item["occurredAt"])),
            correlation_id=item["correlationId"],
            causation_id=item["causationId"],
            payload=item["payload"],
            attempts=0,
        )

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

    def test_completed_event_creates_one_causally_traced_draft_on_replay(self) -> None:
        self.completed_order()
        message = self.completed_event()
        downstream = Mock()
        publisher = QualityRecommendationEventPublisher(self.store, downstream)

        publisher.publish(message)
        publisher.publish(message)

        proposals = IncidentResponseAgent(self.store).list()
        self.assertEqual(1, len(proposals))
        self.assertEqual("CREATE_QUALITY_INSPECTION", proposals[0]["action"])
        created_events = [
            event
            for event in self.store.list_outbox()
            if event["eventType"] == "AgentProposalCreated"
        ]
        self.assertEqual(1, len(created_events))
        self.assertEqual(message.event_id, created_events[0]["causationId"])
        self.assertEqual(message.event_id, created_events[0]["payload"]["triggerEventId"])
        self.assertEqual(2, downstream.publish.call_count)

    def test_completed_event_skips_draft_when_inspection_already_exists(self) -> None:
        order = self.completed_order()
        message = self.completed_event()
        self.quality.create(
            CreateInspectionCommand(
                "q-before-event",
                str(order["workOrderId"]),
                10,
                1,
                "inspector",
            )
        )
        downstream = Mock()

        QualityRecommendationEventPublisher(self.store, downstream).publish(message)

        self.assertEqual([], IncidentResponseAgent(self.store).list())
        downstream.publish.assert_called_once_with(message)

    def test_failed_inspection_is_quarantined_and_rework_requires_approval(self) -> None:
        order = self.completed_order()
        inspection = self.quality.create(
            CreateInspectionCommand("q", str(order["workOrderId"]), 10, 1, "inspector")
        )
        inspection = self.quality.record(
            str(inspection["inspectionId"]),
            False,
            "DIM-001",
            "轴径超差",
            1,
            "inspector",
            "q2",
            "GAUGE-01",
            datetime.now(UTC),
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

    def test_completed_operation_enters_and_leaves_global_quality_queue(self) -> None:
        order = self.completed_order()

        before = self.quality.list_eligible_page(query="WO-Q-1")
        self.assertEqual(1, before["total"])
        self.assertEqual("WO-Q-1", before["items"][0]["humanCode"])

        self.quality.create(
            CreateInspectionCommand("q-queue", str(order["workOrderId"]), 10, 1, "inspector")
        )
        after = self.quality.list_eligible_page(query="WO-Q-1")
        self.assertEqual(0, after["total"])

    def test_agent_quality_queue_tool_is_scoped_and_audited(self) -> None:
        self.completed_order()
        tool = ListQualityCandidatesTool(
            self.store,
            ScopedReadPolicy({"quality-agent": {"WS-MACH-01"}}),
        )

        result = tool.execute(
            ToolContext("quality-queue-request", "quality-agent-v1", "quality-agent", "triage"),
            "WS-MACH-01",
            10,
        )

        self.assertEqual("ALLOW", result["policyDecision"])
        self.assertEqual(1, result["data"]["total"])
        self.assertEqual("AgentToolCallRecorded", self.store.list_outbox()[-1]["eventType"])

        with self.assertRaisesRegex(Exception, "not authorized"):
            tool.execute(
                ToolContext(
                    "quality-queue-denied",
                    "quality-agent-v1",
                    "outside-user",
                    "out-of-scope triage",
                ),
                "WS-MACH-01",
                10,
            )
        self.assertEqual("DENY", self.store.list_outbox()[-1]["payload"]["policyDecision"])

    def test_expired_gauge_cannot_record_result(self) -> None:
        order = self.completed_order()
        inspection = self.quality.create(
            CreateInspectionCommand("q-exp", str(order["workOrderId"]), 10, 1, "inspector")
        )
        now = datetime.now(UTC)
        with self.assertRaisesRegex(Exception, "calibration expired"):
            self.quality.record(
                str(inspection["inspectionId"]),
                True,
                None,
                None,
                1,
                "inspector",
                "q-exp-result",
                "GAUGE-EXPIRED",
                now,
            )
