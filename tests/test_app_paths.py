from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core import app_paths


class TestAppPaths(unittest.TestCase):
    def test_runtime_state_dir_falls_back_to_temp_when_primary_dir_is_unwritable(self):
        original_default = app_paths.default_user_data_dir
        try:
            app_paths.default_user_data_dir = lambda: Path("Z:/definitely-missing/verbatim")
            path = app_paths.runtime_state_dir()
        finally:
            app_paths.default_user_data_dir = original_default

        expected = Path(tempfile.gettempdir()) / app_paths.APP_NAME
        self.assertEqual(expected, path)
        self.assertTrue(path.exists())


if __name__ == "__main__":
    unittest.main()
