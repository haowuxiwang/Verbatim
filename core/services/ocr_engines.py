from __future__ import annotations

import json
import os
import shlex
import site
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.ocr_client import PaddleOcrClient
from core.services.ocr_models import OcrSpan


@dataclass(frozen=True)
class EngineResult:
    text: str
    engine: str
    mode: str
    spans: tuple[OcrSpan, ...] | None = None


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

    def _ensure_ocr(self):
        if self._ocr is not None:
            return self._ocr
        if self._init_error:
            raise RuntimeError(self._init_error)

        if self.runtime_dir:
            runtime = str(self.runtime_dir.resolve())
            cache_dir = Path(runtime) / ".paddlex"
            runtime_home = Path(runtime) / ".runtime_home"
            xdg_cache = runtime_home / ".cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            xdg_cache.mkdir(parents=True, exist_ok=True)
            runtime_home.mkdir(parents=True, exist_ok=True)
            os.environ["HOME"] = str(runtime_home)
            os.environ["USERPROFILE"] = str(runtime_home)
            os.environ["XDG_CACHE_HOME"] = str(xdg_cache)
            os.environ.setdefault("PADDLE_HOME", str(Path(runtime) / ".paddle"))
            os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(cache_dir))
            py_path = str(Path(runtime) / "site-packages")
            if Path(py_path).exists():
                existing = os.getenv("PYTHONPATH", "")
                if py_path not in existing.split(os.pathsep):
                    os.environ["PYTHONPATH"] = f"{py_path}{os.pathsep}{existing}" if existing else py_path
                # Make runtime site-packages available for in-process import (frozen builds).
                try:
                    site.addsitedir(py_path)
                except Exception:
                    pass
                if py_path not in sys.path:
                    sys.path.insert(0, py_path)
            font_path = self._resolve_font_path()
            if font_path is not None:
                os.environ["PADDLE_PDX_LOCAL_FONT_FILE_PATH"] = str(font_path)
            elif self.offline_strict:
                raise RuntimeError(
                    "offline runtime missing font: expected simfang.ttf under "
                    f"{self.runtime_dir}\\fonts or {self.runtime_dir}\\assets\\fonts"
                )
        elif self.offline_strict:
            raise RuntimeError(
                "offline runtime dir not configured; set VERBATIM_OCR_RUNTIME_DIR or provide ./ocr_runtime"
            )

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
        try:
            self._ocr = PaddleOCR(**kwargs)
        except Exception as e:
            self._init_error = str(e)
            raise
        return self._ocr

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
                text = str(payload.get("text") or "").strip() if isinstance(payload, dict) else str(payload or "").strip()
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
        py_exec = str(os.getenv("VERBATIM_WORKER_PYTHON", "")).strip()
        if py_exec:
            cmd = [py_exec, "-m", "core.services.local_ocr_worker", "--image", str(image_path)]
        else:
            exe_name = Path(sys.executable).stem.lower()
            if "python" in exe_name:
                cmd = [sys.executable, "-m", "core.services.local_ocr_worker", "--image", str(image_path)]
            else:
                cmd = [sys.executable, "--local-ocr-worker", "--image", str(image_path)]
        if self.runtime_dir is not None:
            cmd.extend(["--runtime-dir", str(self.runtime_dir)])
        cmd.extend(["--offline-strict", "1" if self.offline_strict else "0"])

        timeout_sec = max(1.0, float(timeout_ms) / 1000.0)
        try:
            env = dict(os.environ)
            env.setdefault("PYTHONUTF8", "1")
            env.setdefault("PYTHONIOENCODING", "utf-8")
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
                    "Local OCR worker bootstrap failed. Use the packaged worker entry or set VERBATIM_WORKER_PYTHON."
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
