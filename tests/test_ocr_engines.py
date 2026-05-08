from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.services.ocr_engines import (
    CloudPaddleEngine,
    LocalPaddleEngine,
    LocalPaddleOcrJsonEngine,
    resolve_ocr_json_exe_path,
    resolve_ocr_runtime_dir,
    run_local_ocr_self_check,
)


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

    def test_resolve_worker_python_prefers_ocr_specific_env(self):
        with patch.dict(
            os.environ,
            {
                "VERBATIM_OCR_WORKER_PYTHON": r"D:\ocr\python.exe",
                "VERBATIM_WORKER_PYTHON": r"D:\legacy\python.exe",
            },
            clear=False,
        ):
            self.assertEqual(r"D:\ocr\python.exe", LocalPaddleEngine.resolve_worker_python())

    def test_local_engine_self_check_does_not_pollute_host_environment(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_dir = Path(td)
            (runtime_dir / "assets" / "fonts").mkdir(parents=True, exist_ok=True)
            (runtime_dir / "assets" / "fonts" / "simfang.ttf").write_bytes(b"font")
            for model_name in ("PP-OCRv5_mobile_det", "PP-OCRv5_mobile_rec"):
                model_dir = runtime_dir / "models" / model_name
                model_dir.mkdir(parents=True, exist_ok=True)
                (model_dir / "inference.yml").write_text("ok", encoding="utf-8")
            eng = LocalPaddleEngine(runtime_dir=runtime_dir, offline_strict=True)
            cp = subprocess.CompletedProcess(
                args=["python", "-m", "core.services.local_ocr_worker", "--self-check"],
                returncode=0,
                stdout='{"ok": true, "code": "VERBATIM_LOCAL_OCR_SELF_CHECK_OK"}\n',
                stderr="",
            )
            old_home = os.environ.get("HOME")
            old_userprofile = os.environ.get("USERPROFILE")
            old_pythonpath = os.environ.get("PYTHONPATH")
            before_sys_path = list(os.sys.path)
            with patch("core.services.ocr_engines.subprocess.run", return_value=cp):
                result = eng.self_check(worker_python="python")
            self.assertTrue(result.available)
            self.assertEqual(old_home, os.environ.get("HOME"))
            self.assertEqual(old_userprofile, os.environ.get("USERPROFILE"))
            self.assertEqual(old_pythonpath, os.environ.get("PYTHONPATH"))
            self.assertEqual(before_sys_path, list(os.sys.path))

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
        with patch("core.services.ocr_engines.subprocess.run", return_value=cp):
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

    def test_local_engine_subprocess_injects_runtime_env_without_touching_host(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_dir = Path(td)
            (runtime_dir / "assets" / "fonts").mkdir(parents=True, exist_ok=True)
            (runtime_dir / "assets" / "fonts" / "simfang.ttf").write_bytes(b"font")
            payload = json.dumps({"ok": True, "text": "SUBPROC_OK"})
            cp = subprocess.CompletedProcess(args=["python"], returncode=0, stdout=f"{payload}\n", stderr="")
            eng = LocalPaddleEngine(runtime_dir=runtime_dir, offline_strict=True)
            old_home = os.environ.get("HOME")
            old_userprofile = os.environ.get("USERPROFILE")
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
            env = m_run.call_args.kwargs["env"]
            self.assertIn("PADDLE_HOME", env)
            self.assertIn("PADDLE_PDX_CACHE_HOME", env)
            self.assertEqual(old_home, os.environ.get("HOME"))
            self.assertEqual(old_userprofile, os.environ.get("USERPROFILE"))
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

    def test_local_engine_self_check_uses_frozen_worker_entry_when_python_not_overridden(self):
        cp = subprocess.CompletedProcess(
            args=["Verbatim.exe", "--local-ocr-worker", "--self-check"],
            returncode=0,
            stdout='{"ok": true, "code": "VERBATIM_LOCAL_OCR_SELF_CHECK_OK"}\n',
            stderr="",
        )
        eng = LocalPaddleEngine(runtime_dir=None, offline_strict=False)
        with (
            patch("core.services.ocr_engines.subprocess.run", return_value=cp) as m_run,
            patch("core.services.ocr_engines.sys.executable", "D:/learn/codex/Verbatim_dev/dist/Verbatim/Verbatim.exe"),
            patch("core.services.ocr_engines.sys.frozen", True, create=True),
        ):
            result = eng.self_check(worker_python="")
        self.assertTrue(result.available)
        self.assertEqual(
            ["D:/learn/codex/Verbatim_dev/dist/Verbatim/Verbatim.exe", "--local-ocr-worker", "--self-check"],
            m_run.call_args.args[0][:3],
        )

    def test_resolve_ocr_runtime_dir_prefers_frozen_bundle_over_cwd(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cwd_runtime = root / "cwd" / "ocr_runtime"
            exe_dir = root / "dist" / "Verbatim"
            bundled_runtime = exe_dir / "ocr_runtime"
            cwd_runtime.mkdir(parents=True)
            bundled_runtime.mkdir(parents=True)
            with (
                patch("core.services.ocr_engines.Path.cwd", return_value=root / "cwd"),
                patch("core.services.ocr_engines.sys.executable", str(exe_dir / "Verbatim.exe")),
                patch("core.services.ocr_engines.sys.frozen", True, create=True),
                patch.dict(os.environ, {}, clear=False),
            ):
                resolved = resolve_ocr_runtime_dir()
        self.assertEqual(bundled_runtime, resolved)

    def test_resolve_ocr_json_exe_path_prefers_frozen_bundle_over_cwd(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cwd = root / "cwd"
            exe_dir = root / "dist" / "Verbatim"
            cwd_json = cwd / "PaddleOCR-json.exe"
            bundled_json = exe_dir / "PaddleOCR-json.exe"
            cwd.mkdir(parents=True)
            exe_dir.mkdir(parents=True)
            cwd_json.write_bytes(b"cwd")
            bundled_json.write_bytes(b"dist")
            with (
                patch("core.services.ocr_engines.Path.cwd", return_value=cwd),
                patch("core.services.ocr_engines.sys.executable", str(exe_dir / "Verbatim.exe")),
                patch("core.services.ocr_engines.sys.frozen", True, create=True),
                patch.dict(os.environ, {}, clear=False),
            ):
                resolved = resolve_ocr_json_exe_path()
        self.assertEqual(bundled_json, resolved)

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

    def test_local_engine_self_check_reports_runtime_import_failure(self):
        eng = LocalPaddleEngine(runtime_dir=None, offline_strict=False)
        cp = subprocess.CompletedProcess(
            args=["python", "-m", "core.services.local_ocr_worker", "--self-check"],
            returncode=1,
            stdout="",
            stderr="numpy missing",
        )
        with patch("core.services.ocr_engines.subprocess.run", return_value=cp):
            result = eng.self_check(worker_python="python")
        self.assertFalse(result.available)
        self.assertEqual("runtime_import", result.code)
        self.assertIn("numpy missing", result.message)
        self.assertIn("--self-check", " ".join(cp.args))

    def test_local_engine_self_check_reports_numpy_abi_mismatch(self):
        eng = LocalPaddleEngine(runtime_dir=None, offline_strict=False)
        cp = subprocess.CompletedProcess(
            args=["python", "-m", "core.services.local_ocr_worker", "--self-check"],
            returncode=1,
            stdout='{"ok": false, "error": "A module that was compiled using NumPy 1.x cannot be run in NumPy 2.4.3"}\n',
            stderr="A module that was compiled using NumPy 1.x cannot be run in NumPy 2.4.3\nImportError: numpy.core.multiarray failed to import",
        )
        with patch("core.services.ocr_engines.subprocess.run", return_value=cp):
            result = eng.self_check(worker_python="python")
        self.assertFalse(result.available)
        self.assertEqual("numpy_abi_mismatch", result.code)
        self.assertIn("numpy<2", result.message)

    def test_local_engine_self_check_uses_real_worker_command(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_dir = Path(td)
            eng = LocalPaddleEngine(runtime_dir=runtime_dir, offline_strict=False)
            cp = subprocess.CompletedProcess(
                args=["python", "-m", "core.services.local_ocr_worker", "--self-check"],
                returncode=0,
                stdout='{"ok": true, "code": "VERBATIM_LOCAL_OCR_SELF_CHECK_OK"}\n',
                stderr="",
            )
            with patch("core.services.ocr_engines.subprocess.run", return_value=cp) as m_run:
                result = eng.self_check(worker_python="python")
            self.assertTrue(result.available)
            cmd = m_run.call_args.args[0]
            self.assertIn("--self-check", cmd)
            self.assertIn("--runtime-dir", cmd)
            self.assertIn(str(runtime_dir), cmd)

    def test_local_engine_self_check_requires_complete_runtime_models(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_dir = Path(td)
            (runtime_dir / "assets" / "fonts").mkdir(parents=True, exist_ok=True)
            (runtime_dir / "assets" / "fonts" / "simfang.ttf").write_bytes(b"font")
            eng = LocalPaddleEngine(runtime_dir=runtime_dir, offline_strict=True)
            result = eng.self_check(worker_python="python")
        self.assertFalse(result.available)
        self.assertEqual("model_missing", result.code)
        self.assertIn("missing OCR model directories", result.message)

    def test_local_engine_self_check_requires_inference_manifest(self):
        with tempfile.TemporaryDirectory() as td:
            runtime_dir = Path(td)
            (runtime_dir / "assets" / "fonts").mkdir(parents=True, exist_ok=True)
            (runtime_dir / "assets" / "fonts" / "simfang.ttf").write_bytes(b"font")
            (runtime_dir / "models" / "PP-OCRv5_mobile_det").mkdir(parents=True, exist_ok=True)
            (runtime_dir / "models" / "PP-OCRv5_mobile_rec").mkdir(parents=True, exist_ok=True)
            eng = LocalPaddleEngine(runtime_dir=runtime_dir, offline_strict=True)
            result = eng.self_check(worker_python="python")
        self.assertFalse(result.available)
        self.assertEqual("model_missing", result.code)
        self.assertIn("incomplete", result.message)

    def test_json_engine_self_check_validates_real_binary(self):
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / "PaddleOCR-json.exe"
            exe.write_bytes(b"fake")
            engine = LocalPaddleOcrJsonEngine(exe)
            cp = subprocess.CompletedProcess(
                args=[str(exe), "--help"],
                returncode=1,
                stdout="PaddleOCR-json v1.4.1",
                stderr="",
            )
            with patch("core.services.ocr_engines.subprocess.run", return_value=cp):
                ready, code, message = engine.self_check()
        self.assertTrue(ready)
        self.assertEqual("ready", code)
        self.assertIn("PaddleOCR-json", message)

    def test_run_local_ocr_self_check_requires_json_health(self):
        with tempfile.TemporaryDirectory() as td:
            json_exe = Path(td) / "PaddleOCR-json.exe"
            json_exe.write_bytes(b"fake")
            cp = subprocess.CompletedProcess(
                args=[str(json_exe), "--help"], returncode=2, stdout="", stderr="bad launch"
            )
            with patch("core.services.ocr_engines.subprocess.run", return_value=cp):
                result = run_local_ocr_self_check(
                    runtime_dir=None,
                    offline_strict=False,
                    json_exe=json_exe,
                    worker_python="",
                )
        self.assertFalse(result.available)
        self.assertFalse(result.json_ready)
        self.assertFalse(result.python_worker_ready)

    def test_run_local_ocr_self_check_accepts_healthy_json_runtime_without_python_worker(self):
        with tempfile.TemporaryDirectory() as td:
            json_exe = Path(td) / "PaddleOCR-json.exe"
            json_exe.write_bytes(b"fake")
            cp = subprocess.CompletedProcess(
                args=[str(json_exe), "--help"],
                returncode=1,
                stdout="PaddleOCR-json v1.4.1",
                stderr="",
            )
            with patch("core.services.ocr_engines.subprocess.run", return_value=cp):
                result = run_local_ocr_self_check(
                    runtime_dir=None,
                    offline_strict=False,
                    json_exe=json_exe,
                    worker_python="",
                )
        self.assertTrue(result.available)
        self.assertTrue(result.json_ready)
        self.assertFalse(result.python_worker_ready)


if __name__ == "__main__":
    unittest.main()
