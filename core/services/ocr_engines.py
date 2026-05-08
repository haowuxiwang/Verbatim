from __future__ import annotations

import json
import os
import shlex
import site
import subprocess
import sys
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.services.ocr_models import OcrSpan

if TYPE_CHECKING:
    from core.ocr_client import PaddleOcrClient


@dataclass(frozen=True)
class EngineResult:
    text: str
    engine: str
    mode: str
    spans: tuple[OcrSpan, ...] | None = None


@dataclass(frozen=True)
class LocalOcrSelfCheck:
    available: bool
    code: str
    message: str
    worker_python: str
    runtime_dir: str
    json_exe: str
    python_worker_ready: bool
    json_ready: bool


def resolve_ocr_runtime_dir() -> Path | None:
    raw = (os.getenv("VERBATIM_OCR_RUNTIME_DIR") or "").strip()
    if raw:
        p = Path(raw)
        if p.exists():
            return p
    frozen_mode = bool(getattr(sys, "frozen", False))
    if frozen_mode:
        exe_dir = Path(sys.executable).parent
        external_default = exe_dir / "ocr_runtime"
        if external_default.exists():
            return external_default
        internal_default = exe_dir / "_internal" / "ocr_runtime"
        if internal_default.exists():
            return internal_default
    local_default = Path.cwd() / "ocr_runtime"
    if local_default.exists():
        return local_default
    return None


def resolve_ocr_json_exe_path() -> Path | None:
    raw = (os.getenv("VERBATIM_PADDLEOCR_JSON_EXE") or "").strip()
    if raw:
        p = Path(raw)
        if p.exists():
            return p

    frozen_mode = bool(getattr(sys, "frozen", False))
    roots: list[Path] = []
    if frozen_mode:
        exe_dir = Path(sys.executable).parent
        roots.extend(
            [
                exe_dir,
                exe_dir / "umi",
                exe_dir / "ocr_runtime",
                exe_dir / "_internal",
                exe_dir / "_internal" / "umi",
                exe_dir / "_internal" / "ocr_runtime",
            ]
        )
    roots.extend(
        [
            Path.cwd(),
            Path.cwd() / "umi",
            Path.cwd() / "ocr_runtime",
        ]
    )

    direct_names = [
        "PaddleOCR-json.exe",
        str(Path("PaddleOCR-json") / "PaddleOCR-json.exe"),
    ]
    for root in roots:
        if not root.exists():
            continue
        for rel in direct_names:
            candidate = root / rel
            if candidate.exists():
                return candidate

    patterns = [
        "Umi-OCR*/UmiOCR-data/plugins/*PaddleOCR-json*/PaddleOCR-json.exe",
        "*Umi-OCR*/UmiOCR-data/plugins/*PaddleOCR-json*/PaddleOCR-json.exe",
        "UmiOCR-data/plugins/*PaddleOCR-json*/PaddleOCR-json.exe",
    ]
    seen: set[Path] = set()
    for root in roots:
        if not root.exists() or root in seen:
            continue
        seen.add(root)
        for pattern in patterns:
            for candidate in root.glob(pattern):
                if candidate.exists():
                    return candidate
        if frozen_mode and root in {exe_dir, exe_dir / "umi", exe_dir / "_internal", exe_dir / "_internal" / "umi"}:
            for candidate in root.rglob("PaddleOCR-json.exe"):
                if candidate.exists():
                    return candidate
    return None


def _classify_local_runtime_failure(detail: str) -> tuple[str, str]:
    msg = str(detail or "").strip()
    lowered = msg.lower()
    if (
        "numpy 1.x cannot be run in" in lowered
        or "_array_api not found" in lowered
        or "numpy.core.multiarray failed to import" in lowered
    ):
        return (
            "numpy_abi_mismatch",
            "local OCR runtime is incompatible with NumPy 2.x; create an isolated OCR env with numpy<2",
        )
    if "no module named" in lowered or "modulenotfounderror" in lowered:
        return (
            "module_missing",
            "local OCR runtime is missing required dependencies; install paddlepaddle/paddleocr/paddlex into the OCR env",
        )
    if not msg:
        return "runtime_import", "local OCR dependency import failed"
    return "runtime_import", msg


class CloudPaddleEngine:
    name = "cloud_paddle"

    def __init__(self, client: PaddleOcrClient) -> None:
        self.client = client

    def recognize(
        self,
        *,
        image_bytes: bytes,
        filename: str,
        mode: str,
        run_bg: Callable[..., Any],
        timeout_ms: int,
        allow_sync_to_async_retry: bool,
    ) -> EngineResult:
        selected_mode = (mode or "sync").strip().lower()
        if selected_mode == "async":
            job_id = run_bg(self.client.submit_async, image_bytes, filename=filename, timeout_ms=timeout_ms)
            result = run_bg(self.client.poll_async_result, job_id, timeout_ms=timeout_ms)
            return EngineResult(text=(result.text or "").strip(), engine=self.name, mode="async", spans=None)

        try:
            result = run_bg(self.client.recognize_sync, image_bytes, filename=filename, timeout_ms=timeout_ms)
            return EngineResult(text=(result.text or "").strip(), engine=self.name, mode="sync", spans=None)
        except Exception:
            if not allow_sync_to_async_retry:
                raise
            job_id = run_bg(self.client.submit_async, image_bytes, filename=filename, timeout_ms=timeout_ms)
            result = run_bg(self.client.poll_async_result, job_id, timeout_ms=timeout_ms)
            return EngineResult(text=(result.text or "").strip(), engine=self.name, mode="sync->async", spans=None)


class LocalPaddleEngine:
    name = "local_paddle"
    _SELF_CHECK_OK = "VERBATIM_LOCAL_OCR_SELF_CHECK_OK"

    def __init__(self, runtime_dir: Path | None = None, offline_strict: bool = True) -> None:
        self.runtime_dir = runtime_dir
        self.offline_strict = bool(offline_strict)
        self._ocr = None
        self._init_error: str | None = None
        default_isolate = "1"
        self._subprocess_isolation = str(os.getenv("VERBATIM_LOCAL_OCR_ISOLATE", default_isolate)).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @staticmethod
    def resolve_worker_python() -> str:
        py_exec = str(os.getenv("VERBATIM_OCR_WORKER_PYTHON", "")).strip()
        if py_exec:
            return py_exec
        py_exec = str(os.getenv("VERBATIM_WORKER_PYTHON", "")).strip()
        if py_exec:
            return py_exec
        exe_name = Path(sys.executable).stem.lower()
        if "python" in exe_name:
            return sys.executable
        return ""

    @staticmethod
    def _build_worker_command(
        *,
        worker_python: str,
        image_path: Path | None = None,
        runtime_dir: Path | None,
        offline_strict: bool,
        self_check: bool = False,
    ) -> list[str]:
        py_exec = str(worker_python or "").strip()
        if py_exec:
            cmd = [py_exec, "-m", "core.services.local_ocr_worker"]
        else:
            exe_name = Path(sys.executable).stem.lower()
            if "python" in exe_name:
                cmd = [sys.executable, "-m", "core.services.local_ocr_worker"]
            else:
                cmd = [sys.executable, "--local-ocr-worker"]
        if self_check:
            cmd.append("--self-check")
        if image_path is not None:
            cmd.extend(["--image", str(image_path)])
        if runtime_dir is not None:
            cmd.extend(["--runtime-dir", str(runtime_dir)])
        cmd.extend(["--offline-strict", "1" if offline_strict else "0"])
        return cmd

    @classmethod
    def _self_check_success_payload(cls) -> dict[str, Any]:
        return {"ok": True, "code": cls._SELF_CHECK_OK}

    def _resolve_font_path(self) -> Path | None:
        if self.runtime_dir is None:
            return None
        candidates = [
            self.runtime_dir / "fonts" / "simfang.ttf",
            self.runtime_dir / "assets" / "fonts" / "simfang.ttf",
            self.runtime_dir / ".paddlex" / "fonts" / "simfang.ttf",
        ]
        for p in candidates:
            if p.exists():
                return p
        return None

    def _resolve_model_dir(self, name: str) -> Path | None:
        if self.runtime_dir is None:
            return None
        candidates = [
            self.runtime_dir / "models" / name,
            self.runtime_dir / name,
        ]
        for p in candidates:
            if p.exists():
                return p
        return None

    @staticmethod
    def _model_has_manifest(model_dir: Path | None) -> bool:
        return bool(model_dir is not None and (model_dir / "inference.yml").exists())

    def _validate_runtime_assets(self) -> tuple[str, str] | None:
        if self.runtime_dir is None or not self.offline_strict:
            return None
        font_path = self._resolve_font_path()
        if font_path is None:
            return (
                "runtime_missing",
                "offline runtime missing font: expected simfang.ttf under runtime fonts/assets",
            )
        det_name = (os.getenv("VERBATIM_PADDLE_DET_MODEL") or "PP-OCRv5_mobile_det").strip()
        rec_name = (os.getenv("VERBATIM_PADDLE_REC_MODEL") or "PP-OCRv5_mobile_rec").strip()
        det_dir = self._resolve_model_dir(det_name)
        rec_dir = self._resolve_model_dir(rec_name)
        if det_dir is None or rec_dir is None:
            return (
                "model_missing",
                f"offline runtime missing OCR model directories: det={det_name} ({det_dir}), rec={rec_name} ({rec_dir})",
            )
        if not self._model_has_manifest(det_dir) or not self._model_has_manifest(rec_dir):
            return (
                "model_missing",
                f"offline runtime OCR models are incomplete: det_manifest={(det_dir / 'inference.yml')}, rec_manifest={(rec_dir / 'inference.yml')}",
            )
        return None

    def _runtime_site_packages(self) -> Path | None:
        if self.runtime_dir is None:
            return None
        py_path = self.runtime_dir / "site-packages"
        if py_path.exists():
            return py_path
        return None

    def _runtime_env_overrides(self) -> dict[str, str]:
        overrides: dict[str, str] = {}
        if self.runtime_dir:
            runtime_dir = self.runtime_dir.resolve()
            cache_dir = runtime_dir / ".paddlex"
            runtime_home = runtime_dir / ".runtime_home"
            xdg_cache = runtime_home / ".cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            xdg_cache.mkdir(parents=True, exist_ok=True)
            runtime_home.mkdir(parents=True, exist_ok=True)
            overrides["HOME"] = str(runtime_home)
            overrides["USERPROFILE"] = str(runtime_home)
            overrides["XDG_CACHE_HOME"] = str(xdg_cache)
            overrides["PADDLE_HOME"] = str(runtime_dir / ".paddle")
            overrides["PADDLE_PDX_CACHE_HOME"] = str(cache_dir)
            py_path = self._runtime_site_packages()
            if py_path is not None:
                existing = str(os.environ.get("PYTHONPATH", "") or "")
                py_path_text = str(py_path)
                if py_path_text not in existing.split(os.pathsep):
                    overrides["PYTHONPATH"] = f"{py_path_text}{os.pathsep}{existing}" if existing else py_path_text
            font_path = self._resolve_font_path()
            if font_path is not None:
                overrides["PADDLE_PDX_LOCAL_FONT_FILE_PATH"] = str(font_path)
            elif self.offline_strict:
                raise RuntimeError(
                    "offline runtime missing font: expected simfang.ttf under "
                    f"{self.runtime_dir}\\fonts or {self.runtime_dir}\\assets\\fonts"
                )
        elif self.offline_strict:
            raise RuntimeError(
                "offline runtime dir not configured; set VERBATIM_OCR_RUNTIME_DIR or provide ./ocr_runtime"
            )
        overrides["PYTHONUTF8"] = "1"
        overrides["PYTHONIOENCODING"] = "utf-8"
        return overrides

    def _build_runtime_env(self, base_env: dict[str, str] | None = None) -> dict[str, str]:
        env = dict(base_env or os.environ)
        env.update(self._runtime_env_overrides())
        return env

    @contextmanager
    def _runtime_import_context(self):
        env_updates = self._runtime_env_overrides()
        previous_env: dict[str, str | None] = {}
        for key, value in env_updates.items():
            previous_env[key] = os.environ.get(key)
            os.environ[key] = value
        original_sys_path = list(sys.path)
        py_path = self._runtime_site_packages()
        if py_path is not None:
            py_path_text = str(py_path)
            try:
                site.addsitedir(py_path_text)
            except Exception:
                pass
            if py_path_text not in sys.path:
                sys.path.insert(0, py_path_text)
        try:
            yield
        finally:
            sys.path[:] = original_sys_path
            for key, old_value in previous_env.items():
                if old_value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = old_value

    def _ensure_ocr(self):
        if self._ocr is not None:
            return self._ocr
        if self._init_error:
            raise RuntimeError(self._init_error)

        try:
            with self._runtime_import_context():
                try:
                    from paddleocr import PaddleOCR  # type: ignore
                except Exception as e:
                    for mod in ("paddleocr", "paddlex", "paddle"):
                        if mod in sys.modules:
                            sys.modules.pop(mod, None)
                    self._init_error = f"local paddleocr import failed: {e}"
                    raise RuntimeError(self._init_error) from e

                det_name = (os.getenv("VERBATIM_PADDLE_DET_MODEL") or "PP-OCRv5_mobile_det").strip()
                rec_name = (os.getenv("VERBATIM_PADDLE_REC_MODEL") or "PP-OCRv5_mobile_rec").strip()
                cls_name = (os.getenv("VERBATIM_PADDLE_CLS_MODEL") or "").strip()
                det_dir = self._resolve_model_dir(det_name)
                rec_dir = self._resolve_model_dir(rec_name)
                if self.offline_strict and (det_dir is None or rec_dir is None):
                    raise RuntimeError(
                        f"offline runtime missing OCR models: det={det_name} ({det_dir}), rec={rec_name} ({rec_dir})"
                    )
                kwargs: dict[str, Any] = {
                    "text_detection_model_name": det_name,
                    "text_recognition_model_name": rec_name,
                    "use_doc_orientation_classify": False,
                    "use_doc_unwarping": False,
                    "use_textline_orientation": False,
                    "device": "cpu",
                }
                if det_dir is not None:
                    kwargs["text_detection_model_dir"] = str(det_dir)
                if rec_dir is not None:
                    kwargs["text_recognition_model_dir"] = str(rec_dir)
                if cls_name:
                    kwargs["textline_orientation_model_name"] = cls_name
                self._ocr = PaddleOCR(**kwargs)
        except Exception as e:
            self._init_error = str(e)
            raise
        return self._ocr

    def self_check(self, *, worker_python: str = "") -> LocalOcrSelfCheck:
        runtime_text = str(self.runtime_dir.resolve()) if self.runtime_dir is not None else ""
        json_text = ""
        worker = str(worker_python or self.resolve_worker_python()).strip()
        try:
            env = self._build_runtime_env()
        except Exception as e:
            return LocalOcrSelfCheck(
                available=False,
                code="runtime_missing",
                message=str(e),
                worker_python=worker,
                runtime_dir=runtime_text,
                json_exe=json_text,
                python_worker_ready=False,
                json_ready=False,
            )
        runtime_issue = self._validate_runtime_assets()
        if runtime_issue is not None:
            code, message = runtime_issue
            return LocalOcrSelfCheck(
                available=False,
                code=code,
                message=message,
                worker_python=worker,
                runtime_dir=runtime_text,
                json_exe=json_text,
                python_worker_ready=False,
                json_ready=False,
            )

        use_frozen_worker = bool(getattr(sys, "frozen", False)) and Path(sys.executable).suffix.lower() == ".exe"
        if not worker and not use_frozen_worker:
            return LocalOcrSelfCheck(
                available=False,
                code="worker_missing",
                message="local OCR worker python is not configured",
                worker_python="",
                runtime_dir=runtime_text,
                json_exe=json_text,
                python_worker_ready=False,
                json_ready=False,
            )

        try:
            proc = subprocess.run(
                self._build_worker_command(
                    worker_python=worker,
                    runtime_dir=self.runtime_dir,
                    offline_strict=self.offline_strict,
                    self_check=True,
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=45.0,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return LocalOcrSelfCheck(
                available=False,
                code="runtime_timeout",
                message="local OCR worker self-check timed out",
                worker_python=worker,
                runtime_dir=runtime_text,
                json_exe=json_text,
                python_worker_ready=False,
                json_ready=False,
            )
        except Exception as e:
            code, message = _classify_local_runtime_failure(str(e))
            return LocalOcrSelfCheck(
                available=False,
                code=code,
                message=message,
                worker_python=worker,
                runtime_dir=runtime_text,
                json_exe=json_text,
                python_worker_ready=False,
                json_ready=False,
            )

        if proc.returncode != 0:
            payload = self._parse_worker_payload(proc.stdout)
            detail = (proc.stderr or "").strip()
            if isinstance(payload, dict):
                payload_detail = str(payload.get("error") or "").strip()
                if not detail:
                    detail = payload_detail
            if not detail:
                detail = (proc.stdout or "").strip()
            code, message = _classify_local_runtime_failure(detail)
            return LocalOcrSelfCheck(
                available=False,
                code=code,
                message=message,
                worker_python=worker,
                runtime_dir=runtime_text,
                json_exe=json_text,
                python_worker_ready=False,
                json_ready=False,
            )

        payload = self._parse_worker_payload(proc.stdout)
        if not payload.get("ok"):
            detail = str(payload.get("error") or "local OCR worker self-check returned invalid payload")
            code, message = _classify_local_runtime_failure(detail)
            return LocalOcrSelfCheck(
                available=False,
                code=code,
                message=message,
                worker_python=worker,
                runtime_dir=runtime_text,
                json_exe=json_text,
                python_worker_ready=False,
                json_ready=False,
            )
        if str(payload.get("code") or "") != self._SELF_CHECK_OK:
            return LocalOcrSelfCheck(
                available=False,
                code="runtime_import",
                message="local OCR worker self-check did not confirm runtime readiness",
                worker_python=worker,
                runtime_dir=runtime_text,
                json_exe=json_text,
                python_worker_ready=False,
                json_ready=False,
            )

        return LocalOcrSelfCheck(
            available=True,
            code="ready",
            message="local OCR worker is ready",
            worker_python=worker,
            runtime_dir=runtime_text,
            json_exe=json_text,
            python_worker_ready=True,
            json_ready=False,
        )

    def recognize(
        self,
        *,
        image_bytes: bytes,
        filename: str,
        mode: str,
        run_bg: Callable[..., Any],
        timeout_ms: int,
        allow_sync_to_async_retry: bool,
    ) -> EngineResult:
        _ = allow_sync_to_async_retry
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            tmp = Path(tf.name)
            tf.write(image_bytes)
        try:
            if self._subprocess_isolation:
                payload = self._predict_via_subprocess(tmp, timeout_ms=timeout_ms)
                text = (
                    str(payload.get("text") or "").strip() if isinstance(payload, dict) else str(payload or "").strip()
                )
                spans_payload = payload.get("spans") if isinstance(payload, dict) else None
                spans = None
                if isinstance(spans_payload, list):
                    collected: list[OcrSpan] = []
                    for item in spans_payload:
                        if not isinstance(item, dict):
                            continue
                        t = str(item.get("text") or "").strip()
                        bbox = item.get("bbox")
                        if not t or not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                            continue
                        try:
                            x0, y0, x1, y1 = [float(v) for v in bbox]
                        except Exception:
                            continue
                        collected.append(OcrSpan(text=t, bbox=(x0, y0, x1, y1)))
                    if collected:
                        spans = tuple(collected)
                return EngineResult(text=text, engine=self.name, mode="cpu-subproc", spans=spans)

            # Non-isolated fallback for compatibility.
            with self._runtime_import_context():
                ocr = self._ensure_ocr()
                output = run_bg(ocr.predict, str(tmp), timeout_ms=timeout_ms)
            text = self._extract_local_text(output)
            return EngineResult(text=text, engine=self.name, mode="cpu", spans=None)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    def _predict_via_subprocess(self, image_path: Path, *, timeout_ms: int) -> dict[str, Any]:
        cmd = self._build_worker_command(
            worker_python=self.resolve_worker_python(),
            image_path=image_path,
            runtime_dir=self.runtime_dir,
            offline_strict=self.offline_strict,
        )

        timeout_sec = max(1.0, float(timeout_ms) / 1000.0)
        try:
            env = self._build_runtime_env()
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as e:
            raise TimeoutError(f"Local OCR subprocess timed out (>{int(timeout_sec)}s)") from e

        payload = self._parse_worker_payload(proc.stdout)
        if proc.returncode != 0:
            err = payload.get("error") if isinstance(payload, dict) else ""
            stderr = (proc.stderr or "").strip()
            detail = str(err or stderr or f"exit={proc.returncode}")
            if (
                "No module named core.services.local_ocr_worker" in detail
                or "can't open file" in detail.lower()
                or "unrecognized arguments: --local-ocr-worker" in detail
            ):
                raise RuntimeError(
                    "Local OCR worker bootstrap failed. Use the packaged worker entry or set VERBATIM_OCR_WORKER_PYTHON."
                )
            raise RuntimeError(f"Local OCR subprocess failed: {detail}")
        if not payload.get("ok"):
            detail = str(payload.get("error") or "unknown worker error")
            raise RuntimeError(f"Local OCR worker error: {detail}")
        return payload

    @staticmethod
    def _parse_worker_payload(stdout_text: str) -> dict[str, Any]:
        lines = [ln.strip() for ln in (stdout_text or "").splitlines() if ln.strip()]
        for ln in reversed(lines):
            try:
                obj = json.loads(ln)
            except Exception:
                continue
            if isinstance(obj, dict) and ("ok" in obj or "text" in obj or "error" in obj):
                return obj
        return {"ok": False, "error": "worker output missing JSON payload"}

    @classmethod
    def _extract_local_text(cls, obj: Any) -> str:
        out: list[str] = []
        cls._collect(obj, out)
        return "\n".join(x for x in out if x).strip()

    @classmethod
    def _collect(cls, obj: Any, out: list[str]) -> None:
        if obj is None:
            return
        if isinstance(obj, str):
            s = obj.strip()
            if s:
                out.append(s)
            return
        if isinstance(obj, dict):
            rec_texts = obj.get("rec_texts")
            if isinstance(rec_texts, list):
                for t in rec_texts:
                    if isinstance(t, str) and t.strip():
                        out.append(t.strip())
            for k in ("text", "rec_text", "ocr_text", "transcription"):
                v = obj.get(k)
                if isinstance(v, str) and v.strip():
                    out.append(v.strip())
            for v in obj.values():
                cls._collect(v, out)
            return
        if isinstance(obj, (list, tuple)):
            for it in obj:
                cls._collect(it, out)


class LocalPaddleOcrJsonEngine:
    name = "local_paddleocr_json"

    def __init__(self, exe_path: Path, *, extra_args: str = "") -> None:
        self.exe_path = exe_path
        self.extra_args = extra_args
        self._extra_args_list = shlex.split(extra_args, posix=False) if extra_args else []

    def _resolve_default_config(self) -> Path | None:
        exe_dir = self.exe_path.parent
        preferred = exe_dir / "models" / "config_chinese.txt"
        if preferred.exists():
            return preferred
        models_dir = exe_dir / "models"
        if models_dir.exists():
            for candidate in sorted(models_dir.glob("config_*.txt")):
                if candidate.exists():
                    return candidate
        return None

    def _normalize_config_arg(self, args: list[str]) -> list[str]:
        if not args:
            args = []
        exe_dir = self.exe_path.parent
        cfg_idx = None
        for i, tok in enumerate(args):
            if tok in {"--config_path", "-config_path", "--config"}:
                cfg_idx = i
                break
        default_cfg = self._resolve_default_config()
        if cfg_idx is None:
            if default_cfg is not None:
                args.extend(["--config_path", str(default_cfg)])
            return args
        cfg_value = args[cfg_idx + 1] if cfg_idx + 1 < len(args) else ""
        cfg_path = Path(cfg_value) if cfg_value else None
        if cfg_path and not cfg_path.is_absolute():
            cfg_path = (exe_dir / cfg_path).resolve()
        bad_cfg = (
            cfg_path is None
            or not cfg_path.exists()
            or cfg_path.name.lower() == "ppocr_config.py"
            or cfg_path.suffix.lower() == ".py"
        )
        if bad_cfg:
            if default_cfg is not None:
                args[cfg_idx + 1 : cfg_idx + 2] = [str(default_cfg)]
            else:
                del args[cfg_idx : cfg_idx + 2]
            return args
        args[cfg_idx + 1] = str(cfg_path)
        return args

    def self_check(self) -> tuple[bool, str, str]:
        if not self.exe_path.exists():
            return False, "json_missing", "PaddleOCR-json executable not found"
        try:
            proc = subprocess.run(
                [str(self.exe_path), "--help"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15.0,
                check=False,
                cwd=str(self.exe_path.parent),
            )
        except subprocess.TimeoutExpired:
            return False, "json_timeout", "PaddleOCR-json health check timed out"
        except Exception as e:
            return False, "json_launch_failed", f"PaddleOCR-json failed to launch: {e}"

        output = "\n".join(x for x in (proc.stdout, proc.stderr) if x).strip()
        if "PaddleOCR-json" not in output:
            detail = output or f"exit={proc.returncode}"
            return False, "json_invalid", f"PaddleOCR-json health check returned unexpected output: {detail}"

        if proc.returncode not in {0, 1}:
            detail = output or f"exit={proc.returncode}"
            return False, "json_invalid", f"PaddleOCR-json health check failed: {detail}"
        return True, "ready", "PaddleOCR-json is ready"

    def recognize(
        self,
        *,
        image_bytes: bytes,
        filename: str,
        mode: str,
        run_bg: Callable[..., Any],
        timeout_ms: int,
        allow_sync_to_async_retry: bool,
    ) -> EngineResult:
        _ = mode, run_bg, allow_sync_to_async_retry
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
            tmp = Path(tf.name)
            tf.write(image_bytes)
        try:
            args = [str(self.exe_path), "-image_path", str(tmp)]
            if self._extra_args_list:
                args.extend(self._extra_args_list)
            args = self._normalize_config_arg(args)
            timeout_sec = max(1.0, float(timeout_ms) / 1000.0)
            proc = subprocess.run(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_sec,
                check=False,
                cwd=str(self.exe_path.parent),
            )
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

        payload = self._parse_json_payload(proc.stdout)
        if proc.returncode != 0:
            detail = (proc.stderr or "").strip()
            raise RuntimeError(f"PaddleOCR-json failed: {detail or proc.returncode}")
        if not isinstance(payload, dict):
            raise RuntimeError("PaddleOCR-json returned invalid payload")
        code = int(payload.get("code", -1) or -1)
        if code != 100:
            return EngineResult(text="", engine=self.name, mode="cli", spans=None)
        data = payload.get("data") or []
        texts: list[str] = []
        spans: list[OcrSpan] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    t = str(item.get("text", "") or "").strip()
                    if t:
                        texts.append(t)
                        bbox = None
                        raw_box = item.get("box") or item.get("bbox") or item.get("boxes")
                        if isinstance(raw_box, list) and raw_box:
                            if len(raw_box) == 4 and all(isinstance(x, (int, float)) for x in raw_box):
                                x0, y0, x1, y1 = [float(x) for x in raw_box]
                                bbox = (x0, y0, x1, y1)
                            elif all(isinstance(p, (list, tuple)) and len(p) >= 2 for p in raw_box):
                                xs = [float(p[0]) for p in raw_box]
                                ys = [float(p[1]) for p in raw_box]
                                bbox = (min(xs), min(ys), max(xs), max(ys))
                        if bbox is not None:
                            spans.append(OcrSpan(text=t, bbox=bbox))
        return EngineResult(
            text="\n".join(texts).strip(),
            engine=self.name,
            mode="cli",
            spans=tuple(spans) if spans else None,
        )

    @staticmethod
    def _parse_json_payload(stdout_text: str) -> dict[str, Any] | None:
        lines = [ln.strip() for ln in (stdout_text or "").splitlines() if ln.strip()]
        for ln in reversed(lines):
            try:
                obj = json.loads(ln)
            except Exception:
                continue
            if isinstance(obj, dict) and "code" in obj:
                return obj
        return None


def run_local_ocr_self_check(
    *,
    runtime_dir: Path | None,
    offline_strict: bool,
    json_exe: Path | None = None,
    worker_python: str = "",
) -> LocalOcrSelfCheck:
    json_path = str(json_exe.resolve()) if json_exe is not None else ""
    worker = str(worker_python or LocalPaddleEngine.resolve_worker_python()).strip()
    python_check = LocalPaddleEngine(runtime_dir=runtime_dir, offline_strict=offline_strict).self_check(
        worker_python=worker
    )
    json_ready = False
    json_code = ""
    json_message = ""
    if json_exe is not None:
        json_ready, json_code, json_message = LocalPaddleOcrJsonEngine(json_exe).self_check()
    python_ready = bool(python_check.python_worker_ready)
    available = bool(json_ready or python_ready)
    if available:
        if python_ready and json_ready:
            message = "local OCR python worker and PaddleOCR-json are ready"
        elif json_ready:
            message = json_message or "PaddleOCR-json is ready"
        else:
            message = python_check.message
        code = "ready"
    else:
        if python_check.code not in {"worker_missing", "runtime_import"} or not json_message:
            message = python_check.message
            code = python_check.code
        else:
            message = json_message
            code = json_code or python_check.code
    return LocalOcrSelfCheck(
        available=available,
        code=code,
        message=message,
        worker_python=worker,
        runtime_dir=python_check.runtime_dir,
        json_exe=json_path,
        python_worker_ready=python_ready,
        json_ready=json_ready,
    )
