import unittest
from datetime import UTC, datetime, timedelta

from autonomous_mes.application.agent_tools import GetProductGenealogyTool, ToolContext
from autonomous_mes.application.equipment import (
    EquipmentApplicationService,
    RegisterEquipmentCommand,
)
from autonomous_mes.application.genealogy import (
    GenealogyApplicationService,
    MaterialLotInput,
    ProcessResourceInput,
    RecordExecutionSessionCommand,
    RegisterProductUnitCommand,
)
from autonomous_mes.application.master_data import (
    ManufacturingResourceApplicationService,
    RegisterManufacturingResourceCommand,
)
from autonomous_mes.application.work_orders import (
    CreateWorkOrderCommand,
    OperationCommand,
    OperationSpec,
    ReleaseWorkOrderCommand,
    WorkOrderApplicationService,
)
from autonomous_mes.domain.errors import IdempotencyConflict, InvalidTransition
from autonomous_mes.infrastructure.memory import InMemoryWorkOrderStore, ScopedReadPolicy


class GenealogyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryWorkOrderStore()
        self.orders = WorkOrderApplicationService(self.store)
        self.genealogy = GenealogyApplicationService(self.store)
        masters = ManufacturingResourceApplicationService(self.store)
        for index, values in enumerate(
            [
                ("TOOL", "tool-7", None, "刀具7", "AVAILABLE", 62.5),
                ("FIXTURE", "fixture-3", None, "夹具3", "AVAILABLE", None),
                ("NC_PROGRAM", "shaft-turn", "r12", "车削程序", "RELEASED", None),
                ("TOOL", "T-1", None, "失效刀具", "AVAILABLE", 0),
                ("NC_PROGRAM", "P-1", "R1", "草稿程序", "DRAFT", None),
            ]
        ):
            kind, resource_id, revision, name, status, life = values
            masters.register(
                RegisterManufacturingResourceCommand(
                    f"master-{index}",
                    "engineer",
                    kind,
                    resource_id,
                    revision,
                    name,
                    status,
                    life,
                    None,
                    "MES",
                    None,
                    datetime.now(UTC),
                )
            )
        equipment = EquipmentApplicationService(self.store).register(
            RegisterEquipmentCommand(
                "eq", "CNC-TRACE-1", "谱系机床", "WS-MACH-01", "WC-01", "SIMULATED"
            )
        )
        self.equipment_id = str(equipment["equipmentId"])
        order = self.orders.create(
            CreateWorkOrderCommand(
                "create",
                "trace",
                "WO-TRACE-1",
                "PO-TRACE-1",
                "WS-MACH-01",
                1,
                datetime(2027, 1, 1, tzinfo=UTC),
                50,
                "PR-1",
                "RT-1",
                "BOM-1",
                ["DWG-1"],
                [OperationSpec(10, "TURN", "车削", "WC-01")],
            )
        )
        order = self.orders.release(
            ReleaseWorkOrderCommand("release", "trace", str(order["workOrderId"]), 1, "planner")
        )
        for action, values in [
            ("dispatch", {"resource_id": self.equipment_id}),
            ("start", {}),
            ("report", {"good_quantity": 1}),
            ("complete", {}),
        ]:
            order = self.orders.execute_operation(
                OperationCommand(
                    action,
                    "trace",
                    str(order["workOrderId"]),
                    10,
                    int(order["version"]),
                    "operator-7",
                    action,
                    **values,
                )
            )
        self.genealogy.register(
            RegisterProductUnitCommand("serial", "SN-TRACE-1", str(order["workOrderId"]), "operator-7")
        )

    def test_execution_session_adds_person_and_material_trace(self) -> None:
        started = datetime.now(UTC) - timedelta(minutes=8)
        result = self.genealogy.record_execution(
            RecordExecutionSessionCommand(
                "record",
                "SESSION-TRACE-1",
                "SN-TRACE-1",
                10,
                "operator-7",
                self.equipment_id,
                started,
                started + timedelta(minutes=6),
                [MaterialLotInput("steel-lot-9", 1.25, "kg")],
                [
                    ProcessResourceInput("TOOL", "tool-7", None),
                    ProcessResourceInput("FIXTURE", "fixture-3", None),
                    ProcessResourceInput("NC_PROGRAM", "shaft-turn", "r12"),
                ],
            )
        )
        trace = self.genealogy.get("SN-TRACE-1")

        self.assertEqual("SESSION-TRACE-1", result["sessionId"])
        self.assertEqual("STEEL-LOT-9", result["materials"][0]["materialLot"])
        self.assertEqual(1, len(trace["executionSessions"]))
        self.assertIn("Person", {item["objectType"] for item in trace["links"]})
        self.assertIn("MaterialLot", {item["objectType"] for item in trace["links"]})
        self.assertIn("Tool", {item["objectType"] for item in trace["links"]})
        self.assertEqual("R12", result["resources"][2]["revision"])
        self.assertEqual("ExecutionSessionRecorded", self.store.list_outbox()[-1]["eventType"])

    def test_execution_rejects_wrong_equipment_and_duplicate_session(self) -> None:
        started = datetime.now(UTC) - timedelta(minutes=4)
        command = RecordExecutionSessionCommand(
            "record",
            "SESSION-TRACE-2",
            "SN-TRACE-1",
            10,
            "operator-7",
            self.equipment_id,
            started,
            datetime.now(UTC),
            [MaterialLotInput("steel-lot-10", 1, "piece")],
        )
        self.genealogy.record_execution(command)
        with self.assertRaises(IdempotencyConflict):
            self.genealogy.record_execution(command)
        with self.assertRaises(InvalidTransition):
            self.genealogy.record_execution(
                RecordExecutionSessionCommand(
                    "wrong",
                    "SESSION-WRONG-EQUIPMENT",
                    "SN-TRACE-1",
                    10,
                    "operator-7",
                    "another-machine",
                    started,
                    datetime.now(UTC),
                    [MaterialLotInput("steel-lot-11", 1, "piece")],
                )
            )

    def test_execution_rejects_expired_tool_or_unreleased_program(self) -> None:
        started = datetime.now(UTC) - timedelta(minutes=4)
        base = {
            "correlation_id": "unsafe",
            "session_id": "SESSION-UNSAFE",
            "product_serial": "SN-TRACE-1",
            "operation_sequence": 10,
            "operator_id": "operator-7",
            "equipment_id": self.equipment_id,
            "started_at": started,
            "ended_at": datetime.now(UTC),
            "materials": [MaterialLotInput("steel-lot-12", 1, "piece")],
        }
        with self.assertRaisesRegex(Exception, "tool life"):
            self.genealogy.record_execution(
                RecordExecutionSessionCommand(
                    **base,
                    resources=[ProcessResourceInput("TOOL", "T-1", None)],
                )
            )
        with self.assertRaisesRegex(Exception, "released revision"):
            self.genealogy.record_execution(
                RecordExecutionSessionCommand(
                    **base,
                    resources=[ProcessResourceInput("NC_PROGRAM", "P-1", "R1")],
                )
            )

    def test_agent_genealogy_tool_enforces_scope_and_audits(self) -> None:
        tool = GetProductGenealogyTool(
            self.store, ScopedReadPolicy({"planner": {"WS-MACH-01"}})
        )
        result = tool.execute(
            ToolContext("trace-request", "trace-agent", "planner", "explain trace"),
            "SN-TRACE-1",
        )
        self.assertEqual("R1", result["risk"])
        self.assertEqual("ALLOW", result["policyDecision"])
        self.assertEqual("AgentToolCallRecorded", self.store.list_outbox()[-1]["eventType"])

        denied = GetProductGenealogyTool(
            self.store, ScopedReadPolicy({"outsider": {"WS-OTHER"}})
        )
        with self.assertRaisesRegex(Exception, "not authorized"):
            denied.execute(
                ToolContext("trace-denied", "trace-agent", "outsider", "read trace"),
                "SN-TRACE-1",
            )


if __name__ == "__main__":
    unittest.main()
