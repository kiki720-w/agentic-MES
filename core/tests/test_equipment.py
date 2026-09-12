import unittest
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from autonomous_mes.application.equipment import (
    EquipmentApplicationService,
    RecordTelemetryCommand,
    RegisterEquipmentCommand,
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


if __name__ == "__main__":
    unittest.main()
