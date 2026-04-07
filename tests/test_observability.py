from __future__ import annotations

import json
import logging
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.services.observability import get_logger, log_event


class TestObservability(unittest.TestCase):
    def setUp(self):
        self._reset_logger()

    def tearDown(self):
        self._reset_logger()

    def _reset_logger(self):
        logger = logging.getLogger("verbatim.app")
        for h in list(logger.handlers):
            logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass

    def test_get_logger_creates_rotating_file_handler(self):
        td = tempfile.mkdtemp()
        try:
            with patch("core.services.observability.Path", side_effect=lambda *p: Path(td) / Path(*p)):
                logger = get_logger()
                self.assertGreaterEqual(len(logger.handlers), 1)
                self.assertFalse(logger.propagate)
        finally:
            self._reset_logger()
            shutil.rmtree(td, ignore_errors=True)

    def test_log_event_writes_json_payload(self):
        td = tempfile.mkdtemp()
        try:
            with patch("core.services.observability.Path", side_effect=lambda *p: Path(td) / Path(*p)):
                log_event("E2E", "message", level="warning", step=1)
                log_file = Path(td) / "logs" / "verbatim_app.log"
                self.assertTrue(log_file.exists())
                lines = [ln for ln in log_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
                self.assertGreaterEqual(len(lines), 1)
                payload = json.loads(lines[-1][lines[-1].find("{") :])
                self.assertEqual("E2E", payload["code"])
                self.assertEqual("message", payload["message"])
                self.assertEqual(1, payload["step"])
        finally:
            self._reset_logger()
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
