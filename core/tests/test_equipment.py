import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from autonomous_mes.application.agent_runtime import IncidentResponseAgent
from autonomous_mes.application.equipment import (
    EquipmentApplicationService,
    RecordTelemetryCommand,
    RegisterEquipmentCommand,
)
from autonomous_mes.application.work_orders import (
    CreateWorkOrderCommand,
    OperationCommand,
    OperationSpec,
    ReleaseWorkOrderCommand,
    WorkOrderApplicationService,
)
from autonomous_mes.domain.errors import InvalidTransition, ValidationError
from autonomous_mes.infrastructure.memory import InMemoryWorkOrderStore


class EquipmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryWorkOrderStore()
        self.service = EquipmentApplicationService(self.store)
        self.equipment = self.service.register(
            RegisterEquipmentCommand(
                correlation_id="equipment-register",
                code="CNC-LATHE-01",
                name="一号数控车床",
                workshop_id="WS-MACH-01",
                work_center_id="WC-LATHE-01",
                protocol="SIMULATED",
            )
        )

    def _record(self, **changes: object) -> dict[str, object]:
        values: dict[str, object] = {
            "correlation_id": f"telemetry-{uuid4().hex}",
            "equipment_id": self.equipment["equipmentId"],
            "expected_version": self.equipment["version"],
            "sample_id": f"sample-{uuid4().hex}",
            "observed_at": datetime.now(UTC),
            "state": "RUNNING",
            "spindle_load_percent": 72.5,
            "temperature_celsius": 41.2,
        }
        values.update(changes)
        result = self.service.record(RecordTelemetryCommand(**values))  # type: ignore[arg-type]
        self.equipment = result
        return result

    def test_records_current_state_and_trace_event(self) -> None:
        result = self._record()
        self.assertEqual("RUNNING", result["state"])
        self.assertEqual(2, result["version"])
        self.assertEqual(72.5, result["spindleLoadPercent"])
        self.assertEqual(
            ["EquipmentRegistered", "EquipmentTelemetryRecorded"],
            [event["eventType"] for event in self.store.list_outbox()],
        )

    def test_duplicate_sample_is_idempotent(self) -> None:
        sample_id = f"sample-{uuid4().hex}"
        first = self._record(sample_id=sample_id)
        duplicate = self.service.record(
            RecordTelemetryCommand(
                correlation_id="duplicate",
                equipment_id=first["equipmentId"],
                expected_version=1,
                sample_id=sample_id,
                observed_at=datetime.now(UTC),
                state="RUNNING",
            )
        )
        self.assertEqual(first["version"], duplicate["version"])
        self.assertEqual(2, len(self.store.list_outbox()))

    def test_rejects_stale_and_unexplained_down_state(self) -> None:
        first = self._record()
        with self.assertRaises(InvalidTransition):
            self._record(
                expected_version=first["version"],
                observed_at=datetime.now(UTC) - timedelta(days=1),
            )
        with self.assertRaises(ValidationError):
            self._record(expected_version=first["version"], state="DOWN")

    def test_equipment_alarm_suspends_bound_operation_until_explicit_resume(self) -> None:
        running = self._record()
        work_orders = WorkOrderApplicationService(self.store)
        work_order = work_orders.create(
            CreateWorkOrderCommand(
                idempotency_key="linked-create",
                correlation_id="linked-create",
                human_code="WO-LINKED-001",
                production_order_id="PO-LINKED-001",
                workshop_id="WS-MACH-01",
                quantity=10,
                due_at=datetime.now(UTC) + timedelta(days=3),
                priority=90,
                product_revision_id="PR-A",
                routing_revision_id="RT-A",
                bom_revision_id="BOM-A",
                drawing_revision_ids=["DWG-A"],
                operations=[OperationSpec(10, "TURN", "车削", "WC-LATHE-01")],
            )
        )
        work_order = work_orders.release(
            ReleaseWorkOrderCommand(
                "linked-release", "linked-release", work_order["workOrderId"], 1, "planner"
            )
        )
        for action in ("dispatch", "start"):
            work_order = work_orders.execute_operation(
                OperationCommand(
                    idempotency_key=f"linked-{action}",
                    correlation_id=f"linked-{action}",
                    work_order_id=work_order["workOrderId"],
                    sequence=10,
                    expected_version=work_order["version"],
                    actor_id="operator",
                    action=action,
                    resource_id=running["equipmentId"] if action == "dispatch" else None,
                )
            )

        alarm_sample = f"alarm-{uuid4().hex}"
        alarmed = self._record(
            expected_version=running["version"],
            sample_id=alarm_sample,
            state="ALARM",
            alarm_code="SERVO-OVERLOAD",
        )
        affected = work_orders.handle_equipment_incident(
            str(alarmed["equipmentId"]),
            str(alarmed["code"]),
            "ALARM",
            "SERVO-OVERLOAD",
            alarm_sample,
        )
        suspended = work_orders.get(work_order["workOrderId"])
        self.assertEqual([work_order["workOrderId"]], affected)
        self.assertEqual("SUSPENDED", suspended["status"])
        self.assertEqual("SUSPENDED", suspended["operations"][0]["status"])
        self.assertEqual(
            "OperationSuspendedByEquipmentIncident", self.store.list_outbox()[-1]["eventType"]
        )

        agent = IncidentResponseAgent(self.store)
        observed = agent.analyze()
        self.assertEqual("HOLD_AND_INSPECT", observed[0]["action"])
        self.assertEqual("OBSERVED", observed[0]["status"])

        self._record(
            expected_version=alarmed["version"],
            observed_at=datetime.now(UTC) + timedelta(seconds=1),
            state="IDLE",
            spindle_load_percent=0,
            alarm_code=None,
        )
        actionable = agent.analyze()
        proposal = next(item for item in actionable if item["action"] == "RESUME_OPERATION")
        self.assertEqual("PENDING_APPROVAL", proposal["status"])
        executed = agent.approve(proposal["proposalId"], "supervisor-1", "现场已确认安全")
        self.assertEqual("EXECUTED", executed["status"])
        resumed = work_orders.get(work_order["workOrderId"])
        self.assertEqual("IN_PROGRESS", resumed["status"])
        self.assertEqual("IN_PROGRESS", resumed["operations"][0]["status"])


if __name__ == "__main__":
    unittest.main()
