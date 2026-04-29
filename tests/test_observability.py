from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.services.observability import LOG_FILENAME, get_logger, log_event


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
        with tempfile.TemporaryDirectory() as td:
            with patch("core.services.observability.runtime_state_dir", return_value=Path(td)):
                logger = get_logger()
                self.assertGreaterEqual(len(logger.handlers), 1)
                self.assertFalse(logger.propagate)
                self._reset_logger()

    def test_log_event_writes_json_payload(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("core.services.observability.runtime_state_dir", return_value=Path(td)):
                log_event("E2E", "message", level="warning", step=1)
                log_file = Path(td) / "logs" / LOG_FILENAME
                self.assertTrue(log_file.exists())
                lines = [ln for ln in log_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
                self.assertGreaterEqual(len(lines), 1)
                payload = json.loads(lines[-1][lines[-1].find("{") :])
                self.assertEqual("E2E", payload["code"])
                self.assertEqual("message", payload["message"])
                self.assertEqual(1, payload["step"])
                self._reset_logger()

    def test_get_logger_falls_back_to_null_handler_when_log_dir_init_fails(self):
        with patch("core.services.observability.runtime_state_dir", side_effect=OSError("denied")):
            logger = get_logger()
        self.assertTrue(any(isinstance(handler, logging.NullHandler) for handler in logger.handlers))
        self.assertFalse(logger.propagate)

    def test_log_event_swallows_logger_write_failures(self):
        class _BoomHandler(logging.Handler):
            def emit(self, _record):
                raise OSError("disk full")

        logger = logging.getLogger("verbatim.app")
        logger.setLevel(logging.INFO)
        logger.addHandler(_BoomHandler())
        logger.propagate = False
        log_event("E2E", "message", level="warning", step=1)


if __name__ == "__main__":
    unittest.main()
