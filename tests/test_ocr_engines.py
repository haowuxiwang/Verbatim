from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.services.ocr_engines import CloudPaddleEngine, LocalPaddleEngine


class TestOcrEngines(unittest.TestCase):
    def test_cloud_engine_sync_success(self):
        client = SimpleNamespace(
            recognize_sync=lambda *_args, **_kwargs: SimpleNamespace(text="SYNC_OK"),
            submit_async=lambda *_args, **_kwargs: "job-1",
            poll_async_result=lambda *_args, **_kwargs: SimpleNamespace(text="ASYNC_OK"),
        )
        engine = CloudPaddleEngine(client)
        out = engine.recognize(
            image_bytes=b"x" * 128,
            filename="a.png",
            mode="sync",
            run_bg=lambda fn, *a, **k: fn(*a, **k),
            timeout_ms=1000,
            allow_sync_to_async_retry=False,
        )
        self.assertEqual("SYNC_OK", out.text)
        self.assertEqual("sync", out.mode)

    def test_cloud_engine_sync_to_async_fallback(self):
        def _sync(*_args, **_kwargs):
            raise RuntimeError("sync failed")

        client = SimpleNamespace(
            recognize_sync=_sync,
            submit_async=lambda *_args, **_kwargs: "job-1",
            poll_async_result=lambda *_args, **_kwargs: SimpleNamespace(text="ASYNC_OK"),
        )
        engine = CloudPaddleEngine(client)
        out = engine.recognize(
            image_bytes=b"x" * 128,
            filename="a.png",
            mode="sync",
            run_bg=lambda fn, *a, **k: fn(*a, **k),
            timeout_ms=1000,
            allow_sync_to_async_retry=True,
        )
        self.assertEqual("ASYNC_OK", out.text)
        self.assertEqual("sync->async", out.mode)

    def test_local_extract_text_from_nested_payload(self):
        payload = {
            "res": {
                "rec_texts": ["line1", "line2"],
                "children": [{"text": "line3"}],
            }
        }
        text = LocalPaddleEngine._extract_local_text(payload)
        self.assertIn("line1", text)
        self.assertIn("line2", text)
        self.assertIn("line3", text)

    def test_local_engine_strict_offline_requires_runtime_dir(self):
        eng = LocalPaddleEngine(runtime_dir=None, offline_strict=True)
        with self.assertRaises(RuntimeError):
            eng._ensure_ocr()

    def test_local_engine_strict_offline_requires_font(self):
        with tempfile.TemporaryDirectory() as td:
            eng = LocalPaddleEngine(runtime_dir=Path(td), offline_strict=True)
            with self.assertRaises(RuntimeError):
                eng._ensure_ocr()

    def test_local_engine_uses_run_bg_with_timeout(self):
        class _FakeOcr:
            def predict(self, _path):
                return {"rec_texts": ["OK"]}

        with patch.dict(os.environ, {"VERBATIM_LOCAL_OCR_ISOLATE": "0"}):
            eng = LocalPaddleEngine(runtime_dir=None, offline_strict=False)
            eng._ocr = _FakeOcr()
            calls = {}

            def _run_bg(fn, *args, **kwargs):
                calls["timeout_ms"] = kwargs.get("timeout_ms")
                return fn(*args)

            out = eng.recognize(
                image_bytes=b"x" * 128,
                filename="a.png",
                mode="sync",
                run_bg=_run_bg,
                timeout_ms=4321,
                allow_sync_to_async_retry=False,
            )
        self.assertIn("OK", out.text)
        self.assertEqual(4321, calls.get("timeout_ms"))

    def test_local_engine_subprocess_mode_success(self):
        payload = json.dumps({"ok": True, "text": "SUBPROC_OK"})
        cp = subprocess.CompletedProcess(args=["python"], returncode=0, stdout=f"noise\n{payload}\n", stderr="")
        eng = LocalPaddleEngine(runtime_dir=None, offline_strict=False)
        with patch("core.services.ocr_engines.subprocess.run", return_value=cp) as m_run:
            out = eng.recognize(
                image_bytes=b"x" * 128,
                filename="a.png",
                mode="sync",
                run_bg=lambda fn, *a, **k: fn(*a, **k),
                timeout_ms=1200,
                allow_sync_to_async_retry=False,
            )
        self.assertEqual("SUBPROC_OK", out.text)
        self.assertEqual("cpu-subproc", out.mode)
        self.assertTrue(m_run.called)
        self.assertIn("-m", m_run.call_args.args[0])

    def test_local_engine_defaults_to_subprocess_isolation(self):
        with patch.dict(os.environ, {}, clear=False):
            eng = LocalPaddleEngine(runtime_dir=None, offline_strict=False)
        self.assertTrue(eng._subprocess_isolation)

    def test_local_engine_uses_frozen_entry_when_python_not_overridden(self):
        payload = json.dumps({"ok": True, "text": "SUBPROC_OK"})
        cp = subprocess.CompletedProcess(args=["Verbatim.exe"], returncode=0, stdout=f"{payload}\n", stderr="")
        eng = LocalPaddleEngine(runtime_dir=None, offline_strict=False)
        with (
            patch("core.services.ocr_engines.subprocess.run", return_value=cp) as m_run,
            patch("core.services.ocr_engines.sys.executable", "D:/learn/codex/Verbatim_dev/dist/Verbatim/Verbatim.exe"),
        ):
            eng.recognize(
                image_bytes=b"x" * 128,
                filename="a.png",
                mode="sync",
                run_bg=lambda fn, *a, **k: fn(*a, **k),
                timeout_ms=1200,
                allow_sync_to_async_retry=False,
            )
        self.assertEqual(
            ["D:/learn/codex/Verbatim_dev/dist/Verbatim/Verbatim.exe", "--local-ocr-worker", "--image"],
            m_run.call_args.args[0][:3],
        )

    def test_local_engine_subprocess_mode_timeout(self):
        eng = LocalPaddleEngine(runtime_dir=None, offline_strict=False)
        with (
            patch(
                "core.services.ocr_engines.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd=["python"], timeout=1.0),
            ),
            self.assertRaises(TimeoutError),
        ):
            eng.recognize(
                image_bytes=b"x" * 128,
                filename="a.png",
                mode="sync",
                run_bg=lambda fn, *a, **k: fn(*a, **k),
                timeout_ms=1000,
                allow_sync_to_async_retry=False,
            )


if __name__ == "__main__":
    unittest.main()
