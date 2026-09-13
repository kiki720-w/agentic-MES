import io
import json
import unittest

from openpyxl import Workbook

from autonomous_mes.application.agent_workbook_import import (
    capacity_plan_fingerprint,
    execute_capacity_plan,
    preview_capacity_workbook,
)
from autonomous_mes.application.scheduling import SchedulingApplicationService
from autonomous_mes.infrastructure.memory import InMemoryWorkOrderStore


def capacity_workbook(daily: int = 160) -> bytes:
    workbook = Workbook()
    schedule = workbook.active
    schedule.title = "车工每日计划"
    schedule.append(["人员", "物料编码", "名称", "数量"])
    schedule.append(["王超伟", "MAT-1", "法兰", 2])
    skills = workbook.create_sheet("专线列表及工时")
    skills.append(["人员", "加工种类", "8小时工时", "加班3小时工时"])
    skills.append(["王超伟", "本体", daily, 180])
    skills.append([None, "法兰", None, None])
    skills.append(["李战勋", "车架", 140, 170])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


class AgentWorkbookImportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scheduling = SchedulingApplicationService(InMemoryWorkOrderStore())

    def test_detects_people_capabilities_and_capacity_from_generic_workbook(self) -> None:
        preview = preview_capacity_workbook(
            capacity_workbook(), "车工计划.xlsx", "WS-MACH-01", []
        )

        self.assertTrue(preview["valid"], preview["issues"])
        self.assertEqual("专线列表及工时", preview["source"]["sheet"])
        self.assertEqual(2, preview["stats"]["personCount"])
        self.assertEqual(2, preview["stats"]["createCount"])
        first = preview["actions"][0]["resource"]
        self.assertEqual("王超伟", first["name"])
        self.assertEqual(["TURN", "本体", "法兰"], first["capabilityCodes"])
        self.assertEqual(160, first["dailyCapacityMinutes"])
        self.assertEqual(180, first["overtimeCapacityMinutes"])

    def test_executes_and_read_back_verifies_then_becomes_idempotent(self) -> None:
        content = capacity_workbook()
        preview = preview_capacity_workbook(content, "车工计划.xlsx", "WS-MACH-01", [])

        result = execute_capacity_plan(
            preview, preview["previewFingerprint"], self.scheduling, "demo-planner"
        )

        self.assertEqual("EXECUTED_AND_VERIFIED", result["status"])
        self.assertEqual(2, result["verifiedCount"])
        current = self.scheduling.list_resources("WS-MACH-01")
        second = preview_capacity_workbook(content, "车工计划.xlsx", "WS-MACH-01", current)
        self.assertEqual(0, second["stats"]["createCount"])
        self.assertEqual(2, second["stats"]["unchangedCount"])

    def test_fingerprint_survives_browser_json_number_roundtrip(self) -> None:
        preview = preview_capacity_workbook(
            capacity_workbook(), "车工计划.xlsx", "WS-MACH-01", []
        )
        browser_roundtrip = json.loads(json.dumps(preview))
        for action in browser_roundtrip["actions"]:
            resource = action["resource"]
            for field in ("dailyCapacityMinutes", "overtimeCapacityMinutes"):
                if isinstance(resource[field], float) and resource[field].is_integer():
                    resource[field] = int(resource[field])

        self.assertEqual(
            preview["previewFingerprint"], capacity_plan_fingerprint(browser_roundtrip)
        )

    def test_changed_capacity_generates_versioned_update(self) -> None:
        first = preview_capacity_workbook(
            capacity_workbook(), "车工计划.xlsx", "WS-MACH-01", []
        )
        execute_capacity_plan(
            first, first["previewFingerprint"], self.scheduling, "demo-planner"
        )

        changed = preview_capacity_workbook(
            capacity_workbook(165),
            "车工计划.xlsx",
            "WS-MACH-01",
            self.scheduling.list_resources("WS-MACH-01"),
        )
        self.assertEqual(1, changed["stats"]["updateCount"])
        result = execute_capacity_plan(
            changed, changed["previewFingerprint"], self.scheduling, "demo-planner"
        )
        updated = next(
            item["resource"] for item in result["results"] if item["resource"]["name"] == "王超伟"
        )
        self.assertEqual(165, updated["dailyCapacityMinutes"])
        self.assertEqual(2, updated["version"])


if __name__ == "__main__":
    unittest.main()
