from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import setup_local_ocr_worker_env


class _FakeVersionInfo(tuple):
    @property
    def major(self):
        return self[0]

    @property
    def minor(self):
        return self[1]

    @property
    def micro(self):
        return self[2]


class TestSetupLocalOcrWorkerEnv(unittest.TestCase):
    def test_py311_requirements_fail_fast_on_non_311_interpreter(self):
        fake_version = _FakeVersionInfo((3, 12, 1, "final", 0))
        with patch.object(setup_local_ocr_worker_env.sys, "version_info", fake_version):
            with patch.object(setup_local_ocr_worker_env.sys, "argv", ["setup_local_ocr_worker_env.py"]):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = setup_local_ocr_worker_env.main()
        self.assertEqual(2, rc)
        payload = json.loads(buf.getvalue())
        self.assertFalse(payload["ready"])
        self.assertIn("requires Python 3.11", payload["error"])
        self.assertEqual("3.12.1", payload["python_version"])

    def test_non_py311_requirements_skip_version_gate(self):
        fake_version = _FakeVersionInfo((3, 12, 1, "final", 0))
        with patch.object(setup_local_ocr_worker_env.sys, "version_info", fake_version):
            with patch.object(
                setup_local_ocr_worker_env.sys,
                "argv",
                ["setup_local_ocr_worker_env.py", "--requirements", "custom-ocr.txt"],
            ):
                with patch.object(setup_local_ocr_worker_env.venv.EnvBuilder, "create") as m_create:
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        rc = setup_local_ocr_worker_env.main()
        self.assertEqual(0, rc)
        self.assertTrue(m_create.called)
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["ready"])
        self.assertTrue(str(payload["requirements"]).endswith(str(Path("custom-ocr.txt"))))


if __name__ == "__main__":
    unittest.main()
