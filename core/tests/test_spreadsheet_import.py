import unittest

from autonomous_mes.application.spreadsheet_import import (
    build_spreadsheet_template,
    preview_spreadsheet,
    snapshot_fingerprint,
)


class SpreadsheetImportTests(unittest.TestCase):
    def test_template_round_trip_produces_valid_snapshot_preview(self) -> None:
        preview = preview_spreadsheet(
            build_spreadsheet_template(), "agentic-aps-template.xlsx", "WS-MACH-01"
        )

        self.assertTrue(preview["valid"], preview["issues"])
        self.assertEqual(1, preview["stats"]["workOrderCount"])
        self.assertEqual(2, preview["stats"]["operationCount"])
        self.assertEqual(2, preview["stats"]["resourceCount"])
        operation = preview["snapshot"]["workOrders"][0]["operations"][0]
        self.assertEqual(12, operation["minutesPerUnit"])
        self.assertEqual(30, operation["setupMinutes"])
        self.assertEqual(
            preview["previewFingerprint"], snapshot_fingerprint(preview["snapshot"])
        )

    def test_unknown_file_type_is_rejected_without_writing(self) -> None:
        preview = preview_spreadsheet(b"not a workbook", "input.pdf", "WS-MACH-01")
        self.assertFalse(preview["valid"])
        self.assertIn("支持 .xlsx 和 .csv", preview["issues"][0]["message"])


if __name__ == "__main__":
    unittest.main()
