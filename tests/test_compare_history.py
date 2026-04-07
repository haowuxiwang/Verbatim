from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from core.compare_history import CompareHistoryManager, get_default_manager


class TestCompareHistoryManager(unittest.TestCase):
    def test_add_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "compare_history.json"
            mgr = CompareHistoryManager(path, max_records=50)
            mgr.add_record(
                status="ok",
                summary="status=PASS",
                ops_count=1,
                field_diffs_count=0,
                ocr_used=True,
                compare_status="PASS",
                ocr_state="success",
                ocr_state_reason="applied",
                decision_basis="ocr",
                gate_reason="",
                fallback_reason="",
                reliability="high",
                left_page=0,
                right_page=0,
                left_bbox=(1.0, 2.0, 3.0, 4.0),
                right_bbox=(5.0, 6.0, 7.0, 8.0),
                warnings_count=1,
                diff_ops=[
                    {
                        "type": "replace",
                        "left_indices": [1],
                        "right_indices": [1],
                        "left_bboxes": [],
                        "right_bboxes": [],
                        "meta": {},
                    }
                ],
                left_region_text="LTXT",
                right_region_text="RTXT",
                left_ocr_applied=False,
                right_ocr_applied=True,
            )
            mgr2 = CompareHistoryManager(path, max_records=50)
            items = mgr2.list_records()
            self.assertEqual(1, len(items))
            self.assertEqual("PASS", items[0].compare_status)
            self.assertEqual("ocr", items[0].decision_basis)
            self.assertEqual(1, items[0].ops_count)
            self.assertEqual("replace", items[0].diff_ops[0]["type"])
            self.assertEqual("LTXT", items[0].left_region_text)
            self.assertTrue(items[0].right_ocr_applied)

    def test_corrupted_file_isolated(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "compare_history.json"
            path.write_text("{bad-json", encoding="utf-8")
            mgr = CompareHistoryManager(path)
            self.assertEqual([], mgr.list_records())
            self.assertFalse(path.exists())
            corrupt_files = list(Path(td).glob("compare_history.json.corrupt.*"))
            self.assertTrue(corrupt_files)

    def test_max_records_trim(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "compare_history.json"
            mgr = CompareHistoryManager(path, max_records=20)
            for i in range(35):
                mgr.add_record(
                    status="ok",
                    summary=f"r{i}",
                    ops_count=i,
                    field_diffs_count=0,
                    ocr_used=False,
                    compare_status="PASS",
                    ocr_state="blocked",
                    ocr_state_reason="not_recommended",
                    decision_basis="text",
                    gate_reason="",
                    fallback_reason="",
                    reliability="high",
                    left_page=0,
                    right_page=0,
                    left_bbox=None,
                    right_bbox=None,
                    warnings_count=0,
                    left_region_text="",
                    right_region_text="",
                    left_ocr_applied=False,
                    right_ocr_applied=False,
                )
            self.assertEqual(20, len(mgr.list_records()))

    def test_new_fields_default_on_old_payload(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "compare_history.json"
            path.write_text(
                """
{
  "version": "1.0",
  "records": [
    {
      "timestamp": "2026-03-27T12:00:00",
      "status": "ok",
      "summary": "legacy",
      "ops_count": 0,
      "field_diffs_count": 0,
      "ocr_used": false,
      "compare_status": "PASS",
      "reliability": "high",
      "ocr_state": "blocked",
      "ocr_state_reason": "not_recommended",
      "left_page": 0,
      "right_page": 0,
      "left_bbox": null,
      "right_bbox": null,
      "warnings_count": 0,
      "diff_ops": [],
      "left_region_text": "",
      "right_region_text": "",
      "left_ocr_applied": false,
      "right_ocr_applied": false
    }
  ]
}
                """.strip(),
                encoding="utf-8",
            )
            mgr = CompareHistoryManager(path, max_records=20)
            items = mgr.list_records()
            self.assertEqual(1, len(items))
            self.assertEqual("text", items[0].decision_basis)
            self.assertEqual("", items[0].gate_reason)
            self.assertEqual("", items[0].fallback_reason)

    def test_default_manager_uses_user_data_dir_override_and_seeds_legacy_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / "user-data"
            legacy_root = root / "repo"
            legacy_mappings = legacy_root / "mappings"
            legacy_mappings.mkdir(parents=True, exist_ok=True)
            legacy_file = legacy_mappings / "compare_history.json"
            legacy_file.write_text(
                """
{
  "version": "1.0",
  "records": [
    {
      "timestamp": "2026-04-01T10:00:00",
      "status": "ok",
      "summary": "legacy",
      "ops_count": 1,
      "field_diffs_count": 0,
      "ocr_used": false,
      "compare_status": "PASS",
      "reliability": "high",
      "ocr_state": "blocked",
      "ocr_state_reason": "legacy",
      "decision_basis": "text",
      "gate_reason": "",
      "fallback_reason": "",
      "left_page": 0,
      "right_page": 0,
      "left_bbox": null,
      "right_bbox": null,
      "warnings_count": 0,
      "diff_ops": [],
      "left_region_text": "",
      "right_region_text": "",
      "left_ocr_applied": false,
      "right_ocr_applied": false
    }
  ]
}
                """.strip(),
                encoding="utf-8",
            )

            old_env = os.environ.get("VERBATIM_DATA_DIR")
            try:
                os.environ["VERBATIM_DATA_DIR"] = str(data_dir)
                from core import app_paths

                original_repo_root = app_paths.repo_root
                app_paths.repo_root = lambda: legacy_root
                try:
                    mgr = get_default_manager()
                finally:
                    app_paths.repo_root = original_repo_root
            finally:
                if old_env is None:
                    os.environ.pop("VERBATIM_DATA_DIR", None)
                else:
                    os.environ["VERBATIM_DATA_DIR"] = old_env

            self.assertEqual("legacy", mgr.list_records()[0].summary)
            self.assertTrue((data_dir / "compare_history.json").exists())


if __name__ == "__main__":
    unittest.main()
