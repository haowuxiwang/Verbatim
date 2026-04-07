from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from core.region_manager import RegionManager, get_default_manager


class TestRegionManager(unittest.TestCase):
    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "regions.json"
            mgr = RegionManager(path)
            mgr.add_selection(
                name="r1",
                left_page=0,
                left_bbox=(1.0, 2.0, 3.0, 4.0),
                right_page=1,
                right_bbox=(5.0, 6.0, 7.0, 8.0),
            )

            mgr2 = RegionManager(path)
            items = mgr2.list_selections()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].name, "r1")

    def test_corrupted_file_is_isolated(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "regions.json"
            path.write_text("{bad-json", encoding="utf-8")
            mgr = RegionManager(path)
            self.assertEqual(mgr.list_selections(), [])
            self.assertFalse(path.exists())
            corrupt_files = list(Path(td).glob("regions.json.corrupt.*"))
            self.assertTrue(corrupt_files)

    def test_rename_delete_and_get_selection(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "regions.json"
            mgr = RegionManager(path)
            mgr.add_selection("alpha", 0, (1, 1, 2, 2), 0, (3, 3, 4, 4))
            self.assertIsNotNone(mgr.get_selection("alpha"))
            self.assertTrue(mgr.rename_selection("alpha", "beta"))
            self.assertIsNotNone(mgr.get_selection("beta"))
            self.assertTrue(mgr.delete_selection("beta"))
            self.assertFalse(mgr.delete_selection("beta"))

    def test_add_selection_generates_unique_name(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "regions.json"
            mgr = RegionManager(path)
            s1 = mgr.add_selection("same", 0, (1, 1, 2, 2), 1, (3, 3, 4, 4))
            s2 = mgr.add_selection("same", 0, (1, 1, 2, 2), 1, (3, 3, 4, 4))
            self.assertNotEqual(s1.name, s2.name)
            self.assertTrue(s2.name.startswith("same"))

    def test_export_and_import_selections(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = RegionManager(root / "src.json")
            src.add_selection("a", 0, (1, 1, 2, 2), 1, (3, 3, 4, 4))
            src.add_selection("b", 2, (5, 5, 6, 6), 3, (7, 7, 8, 8))
            export_path = root / "export.json"
            src.export_selections(export_path)
            self.assertTrue(export_path.exists())

            dst = RegionManager(root / "dst.json")
            imported = dst.import_selections(export_path)
            self.assertEqual(2, imported)
            imported_again = dst.import_selections(export_path)
            self.assertEqual(0, imported_again)

    def test_import_invalid_file_returns_zero(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "regions.json"
            mgr = RegionManager(path)
            bad = Path(td) / "bad.json"
            bad.write_text("{", encoding="utf-8")
            self.assertEqual(0, mgr.import_selections(bad))

    def test_default_manager_uses_user_data_dir_override_and_seeds_legacy_file(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data_dir = root / "user-data"
            legacy_root = root / "repo"
            legacy_mappings = legacy_root / "mappings"
            legacy_mappings.mkdir(parents=True, exist_ok=True)
            legacy_file = legacy_mappings / "region_selections.json"
            legacy_file.write_text(
                '{"version":"1.0","selections":[{"name":"legacy","left_page":0,"left_bbox":[1,2,3,4],'
                '"right_page":1,"right_bbox":[5,6,7,8],"timestamp":"2026-04-01T10:00:00"}]}',
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

            self.assertEqual("legacy", mgr.list_selections()[0].name)
            self.assertTrue((data_dir / "region_selections.json").exists())


if __name__ == "__main__":
    unittest.main()
