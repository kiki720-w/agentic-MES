import unittest
from datetime import UTC, datetime, timedelta

from autonomous_mes.application.equipment import (
    EquipmentApplicationService,
    RegisterEquipmentCommand,
)
from autonomous_mes.application.genealogy import (
    GenealogyApplicationService,
    MaterialLotInput,
    RecordExecutionSessionCommand,
    RegisterProductUnitCommand,
)
from autonomous_mes.application.work_orders import (
    CreateWorkOrderCommand,
    OperationCommand,
    OperationSpec,
    ReleaseWorkOrderCommand,
    WorkOrderApplicationService,
)
from autonomous_mes.domain.errors import IdempotencyConflict, InvalidTransition
from autonomous_mes.infrastructure.memory import InMemoryWorkOrderStore


class GenealogyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryWorkOrderStore()
        self.orders = WorkOrderApplicationService(self.store)
        self.genealogy = GenealogyApplicationService(self.store)
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
            )
        )
        trace = self.genealogy.get("SN-TRACE-1")

        self.assertEqual("SESSION-TRACE-1", result["sessionId"])
        self.assertEqual("STEEL-LOT-9", result["materials"][0]["materialLot"])
        self.assertEqual(1, len(trace["executionSessions"]))
        self.assertIn("Person", {item["objectType"] for item in trace["links"]})
        self.assertIn("MaterialLot", {item["objectType"] for item in trace["links"]})
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


if __name__ == "__main__":
    unittest.main()
