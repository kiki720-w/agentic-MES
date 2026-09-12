import os
import tempfile
import unittest
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


class DatabaseMigrationTests(unittest.TestCase):
    def test_initial_migration_creates_core_tables_and_indexes(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "mes-test.db"
            database_url = f"sqlite:///{database_path.as_posix()}"
            config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
            config.set_main_option("script_location", str(Path(__file__).parents[1] / "migrations"))

            previous = os.environ.get("AUTONOMOUS_MES_DATABASE_URL")
            os.environ["AUTONOMOUS_MES_DATABASE_URL"] = database_url
            try:
                command.upgrade(config, "head")
            finally:
                if previous is None:
                    os.environ.pop("AUTONOMOUS_MES_DATABASE_URL", None)
                else:
                    os.environ["AUTONOMOUS_MES_DATABASE_URL"] = previous

            engine = create_engine(database_url)
            schema = inspect(engine)
            self.assertEqual(
                {
                    "agent_tool_audits",
                    "agent_action_proposals",
                    "alembic_version",
                    "event_outbox",
                    "equipment",
                    "equipment_telemetry",
                    "idempotency_records",
                    "quality_inspections",
                    "product_units",
                    "genealogy_links",
                    "work_orders",
                },
                set(schema.get_table_names()),
            )
            outbox_indexes = {item["name"] for item in schema.get_indexes("event_outbox")}
            self.assertIn("ix_event_outbox_pending", outbox_indexes)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
