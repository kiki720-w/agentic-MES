import unittest
from datetime import UTC, datetime, timedelta

from autonomous_mes.application.master_data import (
    ManufacturingResourceApplicationService,
    RegisterManufacturingResourceCommand,
    UpdateManufacturingResourceCommand,
)
from autonomous_mes.domain.errors import IdempotencyConflict, ValidationError
from autonomous_mes.infrastructure.memory import InMemoryWorkOrderStore


class ManufacturingResourceMasterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryWorkOrderStore()
        self.service = ManufacturingResourceApplicationService(self.store)

    def command(self, resource_type: str, resource_id: str, **overrides: object):
        values = {
            "correlation_id": f"register-{resource_id}",
            "actor_id": "master-data-admin",
            "resource_type": resource_type,
            "resource_id": resource_id,
            "revision": "R1" if resource_type == "NC_PROGRAM" else None,
            "name": resource_id,
            "status": "RELEASED" if resource_type == "NC_PROGRAM" else "AVAILABLE",
            "life_remaining_percent": 80.0 if resource_type == "TOOL" else None,
            "calibration_due_at": (
                datetime.now(UTC) + timedelta(days=90) if resource_type == "GAUGE" else None
            ),
            "source_system": "MES",
            "external_reference": None,
            "source_updated_at": datetime.now(UTC),
        }
        values.update(overrides)
        return RegisterManufacturingResourceCommand(**values)  # type: ignore[arg-type]

    def test_registers_internal_and_external_resource_provenance(self) -> None:
        tool = self.service.register(self.command("TOOL", "TOOL-100"))
        program = self.service.register(
            self.command(
                "NC_PROGRAM",
                "PROGRAM-10",
                source_system="DNC",
                external_reference="dnc://programs/10/r1",
            )
        )
        self.assertEqual("MES", tool["sourceSystem"])
        self.assertEqual("DNC", program["sourceSystem"])
        self.assertEqual(2, len(self.service.list()))
        self.assertEqual("ManufacturingResourceRegistered", self.store.list_outbox()[-1]["eventType"])

    def test_rejects_invalid_master_and_duplicate_key(self) -> None:
        with self.assertRaises(ValidationError):
            self.service.register(self.command("GAUGE", "G-1", calibration_due_at=None))
        command = self.command("FIXTURE", "F-1")
        self.service.register(command)
        with self.assertRaises(IdempotencyConflict):
            self.service.register(command)

    def test_state_update_is_versioned_and_rejects_stale_source_time(self) -> None:
        created = self.service.register(self.command("TOOL", "T-SYNC"))
        source_time = datetime.now(UTC) + timedelta(minutes=1)
        changed = self.service.update(
            UpdateManufacturingResourceCommand(
                "update-tool",
                "tool-adapter",
                "TOOL",
                "T-SYNC",
                None,
                int(created["version"]),
                "UNAVAILABLE",
                12.0,
                None,
                source_time,
            )
        )
        self.assertEqual(2, changed["version"])
        self.assertEqual("ManufacturingResourceUpdated", self.store.list_outbox()[-1]["eventType"])
        with self.assertRaisesRegex(Exception, "stale source update"):
            self.service.update(
                UpdateManufacturingResourceCommand(
                    "stale-tool",
                    "tool-adapter",
                    "TOOL",
                    "T-SYNC",
                    None,
                    2,
                    "AVAILABLE",
                    90.0,
                    None,
                    source_time - timedelta(minutes=2),
                )
            )


if __name__ == "__main__":
    unittest.main()
