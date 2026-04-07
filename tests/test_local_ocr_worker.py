from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from core.services import local_ocr_worker


class TestLocalOcrWorker(unittest.TestCase):
    def test_worker_main_success(self):
        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "x.png"
            img.write_bytes(b"fake")

            class _FakeOcr:
                def predict(self, _path):
                    return {"rec_texts": ["ok"]}

            with patch("core.services.local_ocr_worker.LocalPaddleEngine._ensure_ocr", return_value=_FakeOcr()):
                with patch(
                    "core.services.local_ocr_worker.LocalPaddleEngine._extract_local_text",
                    return_value="OK_TEXT",
                ):
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        rc = local_ocr_worker.main(["--image", str(img), "--offline-strict", "0"])

        self.assertEqual(0, rc)
        payload = json.loads(buf.getvalue().strip().splitlines()[-1])
        self.assertTrue(payload.get("ok"))
        self.assertEqual("OK_TEXT", payload.get("text"))
        self.assertIn("spans", payload)
        self.assertIsInstance(payload.get("spans"), list)

    def test_worker_main_failure(self):
        with tempfile.TemporaryDirectory() as td:
            img = Path(td) / "x.png"
            img.write_bytes(b"fake")
            with patch(
                "core.services.local_ocr_worker.LocalPaddleEngine._ensure_ocr",
                side_effect=RuntimeError("boom"),
            ):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = local_ocr_worker.main(["--image", str(img), "--offline-strict", "0"])

        self.assertEqual(2, rc)
        payload = json.loads(buf.getvalue().strip().splitlines()[-1])
        self.assertFalse(payload.get("ok"))
        self.assertIn("boom", str(payload.get("error")))


if __name__ == "__main__":
    unittest.main()
