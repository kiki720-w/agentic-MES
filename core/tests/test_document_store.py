import tempfile
import unittest
from pathlib import Path

from autonomous_mes.application.document_store import LocalDocumentStore


class LocalDocumentStoreTests(unittest.TestCase):
    def test_retrieves_relevant_late_section_with_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LocalDocumentStore(Path(directory))
            filler = "普通计划数据\n" * 500
            metadata = store.ingest({
                "name": "人员能力.xlsx", "kind": "WORKBOOK", "parser": "LOCAL",
                "sha256": "a" * 64, "status": "PARSED", "summary": "两张工作表",
                "text": f"## 工作表：计划\n{filler}\n## 工作表：人员能力\n王师傅\t数控车削\t日产能160件",
                "truncated": False, "metadata": {}, "warnings": [],
            })
            results = store.search([metadata["documentId"]], "谁会数控车削，日产能多少", limit=3)

            self.assertGreater(metadata["chunkCount"], 3)
            self.assertIn("王师傅", results[0]["text"])
            self.assertEqual("工作表：人员能力", results[0]["location"])

    def test_document_is_persistent_and_metadata_hides_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = LocalDocumentStore(root).ingest({
                "name": "note.txt", "kind": "TEXT", "parser": "LOCAL_TEXT",
                "sha256": "b" * 64, "status": "PARSED", "summary": "",
                "text": "设备 M-08 报警 E42", "truncated": False,
                "metadata": {}, "warnings": [],
            })
            loaded = LocalDocumentStore(root).get(metadata["documentId"])
            self.assertEqual("note.txt", loaded["name"])
            self.assertNotIn("chunks", loaded)

    def test_image_source_stays_local_and_can_be_loaded_for_vision_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = LocalDocumentStore(Path(directory))
            metadata = store.ingest({
                "name": "drawing.png", "kind": "IMAGE_OCR", "parser": "LOCAL_RAPIDOCR",
                "sha256": "c" * 64, "status": "PARSED", "summary": "",
                "text": "尺寸 25 mm", "truncated": False, "metadata": {}, "warnings": [],
            }, b"fake-png")

            self.assertEqual(
                "data:image/png;base64,ZmFrZS1wbmc=",
                store.image_data_url(metadata["documentId"]),
            )
