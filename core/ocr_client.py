from __future__ import annotations

import base64
import ctypes
import json
import os
import re
import socket
import ssl
import statistics
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request

import fitz  # PyMuPDF

from .models import BBox

DEFAULT_SYNC_URL = "https://df2dt7zevdv631k3.aistudio-app.com/layout-parsing"
DEFAULT_JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
DEFAULT_MODEL = "PaddleOCR-VL-1.5"


def _is_windows() -> bool:
    return os.name == "nt"


def _dpapi_encrypt_text(plain_text: str) -> str | None:
    if not _is_windows():
        return None
    data = (plain_text or "").encode("utf-8")
    if not data:
        return None

    try:
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        def _blob_from_bytes(raw: bytes):
            buf = ctypes.create_string_buffer(raw)
            blob = DATA_BLOB(len(raw), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
            return blob, buf

        in_blob, in_buf = _blob_from_bytes(data)
        out_blob = DATA_BLOB()
        descr = ctypes.c_wchar_p("Verbatim OCR Token")

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        ok = crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            descr,
            None,
            None,
            None,
            0,
            ctypes.byref(out_blob),
        )
        if not ok:
            return None
        try:
            enc = ctypes.string_at(out_blob.pbData, out_blob.cbData)
            return base64.b64encode(enc).decode("ascii")
        finally:
            if out_blob.pbData:
                kernel32.LocalFree(out_blob.pbData)
            del in_buf
    except Exception:
        return None


def _dpapi_decrypt_text(cipher_b64: str) -> str | None:
    if not _is_windows():
        return None
    cipher = (cipher_b64 or "").strip()
    if not cipher:
        return None
    try:
        raw = base64.b64decode(cipher)
    except Exception:
        return None

    try:
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

        def _blob_from_bytes(data_bytes: bytes):
            buf = ctypes.create_string_buffer(data_bytes)
            blob = DATA_BLOB(len(data_bytes), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
            return blob, buf

        in_blob, in_buf = _blob_from_bytes(raw)
        out_blob = DATA_BLOB()
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(out_blob),
        )
        if not ok:
            return None
        try:
            dec = ctypes.string_at(out_blob.pbData, out_blob.cbData)
            return dec.decode("utf-8", errors="ignore").strip()
        finally:
            if out_blob.pbData:
                kernel32.LocalFree(out_blob.pbData)
            del in_buf
    except Exception:
        return None


def _dpapi_is_available() -> bool:
    """Return whether DPAPI encryption is usable in the current Windows session."""
    if not _is_windows():
        return False
    probe = "verbatim-dpapi-probe"
    enc = _dpapi_encrypt_text(probe)
    if not enc:
        return False
    return _dpapi_decrypt_text(enc) == probe


def default_ocr_config_path() -> Path:
    appdata = (os.getenv("APPDATA") or "").strip()
    if appdata:
        return Path(appdata) / "Verbatim" / "ocr_config.json"
    return Path.home() / ".verbatim" / "ocr_config.json"


@dataclass
class OcrConfig:
    token: str
    sync_url: str = DEFAULT_SYNC_URL
    job_url: str = DEFAULT_JOB_URL
    model: str = DEFAULT_MODEL
    timeout_sec: int = 20
    poll_interval_sec: float = 2.0
    poll_timeout_sec: int = 45
    insecure_fallback: bool = True
    retry_count: int = 1
    retry_backoff_sec: float = 1.0
    proxy_url: str = ""
    source: str = "env"
    token_storage: str = "env"

    def __post_init__(self) -> None:
        self.timeout_sec = max(1, int(self.timeout_sec))
        self.poll_interval_sec = max(0.2, float(self.poll_interval_sec))
        self.poll_timeout_sec = max(1, int(self.poll_timeout_sec))
        self.retry_count = max(1, int(self.retry_count))
        self.retry_backoff_sec = max(0.1, float(self.retry_backoff_sec))
        self.proxy_url = str(self.proxy_url or "").strip()

    @staticmethod
    def from_env() -> OcrConfig | None:
        token = (os.getenv("VERBATIM_OCR_TOKEN") or "").strip()
        if not token:
            return None
        return OcrConfig(
            token=token,
            sync_url=(os.getenv("VERBATIM_OCR_SYNC_URL") or DEFAULT_SYNC_URL).strip(),
            job_url=(os.getenv("VERBATIM_OCR_JOB_URL") or DEFAULT_JOB_URL).strip(),
            model=(os.getenv("VERBATIM_OCR_MODEL") or DEFAULT_MODEL).strip(),
            timeout_sec=int(os.getenv("VERBATIM_OCR_TIMEOUT_SEC") or "20"),
            poll_interval_sec=float(os.getenv("VERBATIM_OCR_POLL_INTERVAL_SEC") or "2.0"),
            poll_timeout_sec=int(os.getenv("VERBATIM_OCR_POLL_TIMEOUT_SEC") or "45"),
            insecure_fallback=(os.getenv("VERBATIM_OCR_INSECURE_FALLBACK", "1").strip() in {"1", "true", "True"}),
            retry_count=int(os.getenv("VERBATIM_OCR_RETRY_COUNT") or "1"),
            retry_backoff_sec=float(os.getenv("VERBATIM_OCR_RETRY_BACKOFF_SEC") or "1.0"),
            proxy_url=(os.getenv("VERBATIM_OCR_PROXY_URL") or "").strip(),
            source="env",
            token_storage="env",
        )

    @staticmethod
    def from_file(path: Path | None = None) -> OcrConfig | None:
        p = path or default_ocr_config_path()
        if not p.exists():
            return None
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(obj, dict):
            return None
        token = ""
        token_enc = str(obj.get("token_enc", "")).strip()
        if token_enc:
            token = _dpapi_decrypt_text(token_enc) or ""
        plain_token = str(obj.get("token", "")).strip()
        if not token:
            token = plain_token
        if not token:
            return None
        cfg = OcrConfig(
            token=token,
            sync_url=str(obj.get("sync_url", DEFAULT_SYNC_URL)).strip(),
            job_url=str(obj.get("job_url", DEFAULT_JOB_URL)).strip(),
            model=str(obj.get("model", DEFAULT_MODEL)).strip(),
            timeout_sec=int(obj.get("timeout_sec", 20)),
            poll_interval_sec=float(obj.get("poll_interval_sec", 2.0)),
            poll_timeout_sec=int(obj.get("poll_timeout_sec", 45)),
            insecure_fallback=bool(obj.get("insecure_fallback", True)),
            retry_count=int(obj.get("retry_count", 1)),
            retry_backoff_sec=float(obj.get("retry_backoff_sec", 1.0)),
            proxy_url=str(obj.get("proxy_url", "")).strip(),
            source="file",
            token_storage="dpapi" if token_enc else "plain",
        )
        # Backward compatibility migration: legacy plaintext config -> encrypted storage on Windows.
        if _is_windows() and plain_token and not token_enc:
            try:
                cfg.save_to_file(p)
            except Exception:
                pass
        return cfg

    @staticmethod
    def load(path: Path | None = None) -> OcrConfig | None:
        env_cfg = OcrConfig.from_env()
        if env_cfg:
            return env_cfg
        return OcrConfig.from_file(path)

    def save_to_file(self, path: Path | None = None) -> Path:
        p = path or default_ocr_config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        token_enc = _dpapi_encrypt_text(self.token)
        if token_enc and _dpapi_decrypt_text(token_enc) != self.token:
            token_enc = None
        token_storage = "dpapi" if token_enc else "plain"
        obj = {
            "sync_url": self.sync_url,
            "job_url": self.job_url,
            "model": self.model,
            "timeout_sec": self.timeout_sec,
            "poll_interval_sec": self.poll_interval_sec,
            "poll_timeout_sec": self.poll_timeout_sec,
            "insecure_fallback": self.insecure_fallback,
            "retry_count": self.retry_count,
            "retry_backoff_sec": self.retry_backoff_sec,
            "proxy_url": self.proxy_url,
        }
        if token_enc:
            obj["token_enc"] = token_enc
        else:
            obj["token"] = self.token
        obj["token_storage"] = token_storage
        self.token_storage = token_storage
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        return p


@dataclass
class OcrResult:
    text: str
    raw: dict[str, Any]
    source: str


@dataclass
class RenderedRegion:
    image_bytes: bytes
    clip_bbox: BBox
    zoom: float


def render_pdf_region_png_with_meta(
    pdf_path: Path,
    page_number: int,
    bbox: BBox,
    zoom: float = 3.0,
    padding: float = 0.0,
    grayscale: bool = False,
) -> RenderedRegion:
    x0, y0, x1, y1 = bbox
    clip = fitz.Rect(float(x0), float(y0), float(x1), float(y1))
    if padding > 0:
        clip = fitz.Rect(clip.x0 - padding, clip.y0 - padding, clip.x1 + padding, clip.y1 + padding)
    doc = fitz.open(pdf_path)
    try:
        page = doc.load_page(int(page_number))
        clip = clip & page.rect
        mat = fitz.Matrix(float(zoom), float(zoom))
        colorspace = fitz.csGRAY if grayscale else fitz.csRGB
        pix = page.get_pixmap(matrix=mat, clip=clip, colorspace=colorspace, alpha=False)
        return RenderedRegion(
            image_bytes=pix.tobytes("png"),
            clip_bbox=(float(clip.x0), float(clip.y0), float(clip.x1), float(clip.y1)),
            zoom=float(zoom),
        )
    finally:
        doc.close()


def render_pdf_region_png(
    pdf_path: Path,
    page_number: int,
    bbox: BBox,
    zoom: float = 3.0,
    padding: float = 0.0,
    grayscale: bool = False,
) -> bytes:
    return render_pdf_region_png_with_meta(
        pdf_path,
        page_number,
        bbox,
        zoom=zoom,
        padding=padding,
        grayscale=grayscale,
    ).image_bytes


class PaddleOcrClient:
    def __init__(self, cfg: OcrConfig) -> None:
        self.cfg = cfg

    def recognize_sync(self, image_bytes: bytes, filename: str = "region.png") -> OcrResult:
        payload = self._multipart_payload(
            fields={"model": self.cfg.model},
            files={"file": (filename, image_bytes, "image/png")},
        )
        headers = self._auth_headers()
        headers["Content-Type"] = payload["content_type"]
        multipart_error: Exception | None = None
        try:
            raw = self._post_json(self.cfg.sync_url, payload["body"], headers)
            text = self._extract_text(raw)
            if text:
                return OcrResult(text=text, raw=raw, source="sync")
        except Exception as e:
            multipart_error = e

        # Fallback to JSON base64 style payload for API variants.
        b64 = base64.b64encode(image_bytes).decode("ascii")
        json_headers = self._auth_headers()
        json_headers["Content-Type"] = "application/json"
        json_variants = [
            {"model": self.cfg.model, "image": b64},
            {"model": self.cfg.model, "file": b64},
            {"model": self.cfg.model, "input": {"image": b64}},
        ]
        last_json_error: Exception | None = None
        for body_obj in json_variants:
            try:
                json_body = json.dumps(body_obj).encode("utf-8")
                raw2 = self._post_json(self.cfg.sync_url, json_body, json_headers)
                text2 = self._extract_text(raw2)
                if text2:
                    return OcrResult(text=text2, raw=raw2, source="sync")
            except Exception as e:
                last_json_error = e

        if last_json_error:
            raise RuntimeError(f"Sync OCR fallback failed: {last_json_error}") from last_json_error
        if multipart_error:
            raise RuntimeError(f"Sync OCR failed: {multipart_error}") from multipart_error
        return OcrResult(text="", raw={}, source="sync")

    def submit_async(self, image_bytes: bytes, filename: str = "region.png") -> str:
        payload = self._multipart_payload(
            fields={"model": self.cfg.model},
            files={"file": (filename, image_bytes, "image/png")},
        )
        headers = self._auth_headers()
        headers["Content-Type"] = payload["content_type"]
        last_error: Exception | None = None

        try:
            raw = self._post_json(self.cfg.job_url, payload["body"], headers)
            job_id = self._find_first_value(raw, {"job_id", "id", "task_id"})
            if job_id:
                return str(job_id)
        except Exception as e:
            last_error = e

        b64 = base64.b64encode(image_bytes).decode("ascii")
        json_headers = self._auth_headers()
        json_headers["Content-Type"] = "application/json"
        json_variants = [
            {"model": self.cfg.model, "image": b64},
            {"model": self.cfg.model, "file": b64},
            {"model": self.cfg.model, "input": {"image": b64}},
        ]
        for body_obj in json_variants:
            try:
                body = json.dumps(body_obj).encode("utf-8")
                raw2 = self._post_json(self.cfg.job_url, body, json_headers)
                job_id = self._find_first_value(raw2, {"job_id", "id", "task_id"})
                if job_id:
                    return str(job_id)
            except Exception as e:
                last_error = e

        if last_error:
            raise RuntimeError(f"Async submit failed: {last_error}") from last_error
        raise RuntimeError("Async submit succeeded but no job id found in all payload variants")

    def poll_async_result(self, job_id: str) -> OcrResult:
        url = f"{self.cfg.job_url.rstrip('/')}/{job_id}"
        headers = self._auth_headers()
        deadline = time.time() + self.cfg.poll_timeout_sec

        while time.time() < deadline:
            raw = self._get_json(url, headers)
            status = str(self._find_first_value(raw, {"status", "state"}) or "").lower()
            if status in {"succeeded", "success", "finished", "done", "completed"}:
                text = self._extract_text(raw)
                return OcrResult(text=text, raw=raw, source="async")
            if status in {"failed", "error", "cancelled"}:
                raise RuntimeError(f"OCR async job failed: {raw}")
            time.sleep(self.cfg.poll_interval_sec)

        raise TimeoutError(f"OCR async polling timed out for job {job_id}")

    def _auth_headers(self) -> dict[str, str]:
        token = self.cfg.token.strip()
        return {
            "Authorization": f"Bearer {token}",
            "token": token,
            "X-API-KEY": token,
            "Accept": "application/json",
            "User-Agent": "Verbatim/1.0",
            "Connection": "close",
        }

    def _post_json(self, url: str, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
        req = request.Request(url=url, data=body, headers=headers, method="POST")
        try:
            data = self._urlopen_with_fallback(req)
        except error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"OCR HTTP {e.code}: {detail}") from e
        except Exception as e:
            raise RuntimeError(f"OCR request failed: {e}") from e
        return self._safe_json_loads(data)

    def _get_json(self, url: str, headers: dict[str, str]) -> dict[str, Any]:
        req = request.Request(url=url, headers=headers, method="GET")
        try:
            data = self._urlopen_with_fallback(req)
        except error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"OCR HTTP {e.code}: {detail}") from e
        except Exception as e:
            raise RuntimeError(f"OCR request failed: {e}") from e
        return self._safe_json_loads(data)

    def _urlopen_with_fallback(self, req: request.Request) -> bytes:
        last_error: Exception | None = None
        transient_http_statuses = {408, 409, 425, 429, 500, 502, 503, 504}

        for attempt in range(self.cfg.retry_count):
            try:
                return self._open_request(req, verify_ssl=True)
            except error.HTTPError as e:
                last_error = e
                if e.code in transient_http_statuses and attempt + 1 < self.cfg.retry_count:
                    time.sleep(self.cfg.retry_backoff_sec * (2**attempt))
                    continue
                raise
            except Exception as e:
                last_error = e
                if self.cfg.insecure_fallback and self._is_ssl_error(e):
                    try:
                        return self._open_request(req, verify_ssl=False)
                    except Exception as insecure_e:
                        last_error = insecure_e
                        if attempt + 1 < self.cfg.retry_count and self._is_retryable_error(insecure_e):
                            time.sleep(self.cfg.retry_backoff_sec * (2**attempt))
                            continue
                        raise
                if attempt + 1 < self.cfg.retry_count and self._is_retryable_error(e):
                    time.sleep(self.cfg.retry_backoff_sec * (2**attempt))
                    continue
                raise

        if last_error:
            raise last_error
        raise RuntimeError("OCR request failed with unknown network error")

    def _open_request(self, req: request.Request, verify_ssl: bool) -> bytes:
        if verify_ssl:
            context = ssl.create_default_context()
        else:
            context = ssl._create_unverified_context()  # noqa: SLF001

        if self.cfg.proxy_url:
            proxy = self.cfg.proxy_url
            opener = request.build_opener(
                request.ProxyHandler({"http": proxy, "https": proxy}),
                request.HTTPSHandler(context=context),
            )
        else:
            # Avoid implicit env proxy differences between shells (e.g. Git Bash vs PowerShell).
            opener = request.build_opener(
                request.ProxyHandler({}),
                request.HTTPSHandler(context=context),
            )

        with opener.open(req, timeout=self.cfg.timeout_sec) as resp:
            return resp.read()

    @classmethod
    def _is_ssl_error(cls, exc: Exception) -> bool:
        if isinstance(exc, ssl.SSLError):
            return True
        msg = cls._error_text(exc).upper()
        ssl_markers = (
            "UNEXPECTED_EOF_WHILE_READING",
            "CERTIFICATE_VERIFY_FAILED",
            "TLSV1",
            "SSL:",
            "WRONG_VERSION_NUMBER",
            "HANDSHAKE",
        )
        return any(marker in msg for marker in ssl_markers)

    @classmethod
    def _is_retryable_error(cls, exc: Exception) -> bool:
        if isinstance(exc, (TimeoutError, socket.timeout, ConnectionResetError, ssl.SSLEOFError)):
            return True
        if isinstance(exc, error.URLError):
            reason = exc.reason
            if isinstance(reason, (TimeoutError, socket.timeout, ssl.SSLError, OSError)):
                return True
            return "timed out" in str(reason).lower()
        msg = cls._error_text(exc).lower()
        retryable_markers = (
            "temporarily unavailable",
            "connection reset",
            "connection aborted",
            "remote end closed connection",
            "timed out",
            "eof occurred in violation",
        )
        return any(marker in msg for marker in retryable_markers)

    @staticmethod
    def _error_text(exc: Exception) -> str:
        parts = [str(exc)]
        reason = getattr(exc, "reason", None)
        if reason:
            parts.append(str(reason))
        if exc.__cause__:
            parts.append(str(exc.__cause__))
        if exc.__context__:
            parts.append(str(exc.__context__))
        return " | ".join(p for p in parts if p)

    @staticmethod
    def _safe_json_loads(raw: bytes) -> dict[str, Any]:
        try:
            obj = json.loads(raw.decode("utf-8", errors="ignore"))
            return obj if isinstance(obj, dict) else {"data": obj}
        except Exception:
            return {"raw": raw.decode("utf-8", errors="ignore")}

    @staticmethod
    def _multipart_payload(fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]) -> dict[str, Any]:
        boundary = f"----Verbatim{uuid.uuid4().hex}"
        lines: list[bytes] = []

        for k, v in fields.items():
            lines.append(f"--{boundary}".encode())
            lines.append(f'Content-Disposition: form-data; name="{k}"'.encode())
            lines.append(b"")
            lines.append(str(v).encode("utf-8"))

        for k, (filename, content, mime_type) in files.items():
            lines.append(f"--{boundary}".encode())
            lines.append(f'Content-Disposition: form-data; name="{k}"; filename="{filename}"'.encode())
            lines.append(f"Content-Type: {mime_type}".encode())
            lines.append(b"")
            lines.append(content)

        lines.append(f"--{boundary}--".encode())
        lines.append(b"")
        body = b"\r\n".join(lines)
        return {"content_type": f"multipart/form-data; boundary={boundary}", "body": body}

    @classmethod
    def _extract_text(cls, obj: Any) -> str:
        positioned = cls._collect_positioned_text_values(obj)
        if positioned:
            return cls._join_positioned_text_values(positioned)

        texts: list[str] = []
        cls._collect_text_values(obj, texts, include_aux=False)
        if texts:
            return "\n".join((t or "").strip() for t in texts if (t or "").strip()).strip()

        # Fallback for API variants that only return generic value/label fields.
        aux_texts: list[str] = []
        cls._collect_text_values(obj, aux_texts, include_aux=True)
        return "\n".join((t or "").strip() for t in aux_texts if (t or "").strip()).strip()

    @classmethod
    def _collect_text_values(cls, obj: Any, out: list[str], *, include_aux: bool) -> None:
        if obj is None:
            return
        if isinstance(obj, str):
            return
        if isinstance(obj, list):
            for it in obj:
                cls._collect_text_values(it, out, include_aux=include_aux)
            return
        if not isinstance(obj, dict):
            return

        primary_keys = {"text", "rec_text", "ocr_text", "transcription"}
        aux_keys = {"value"} if include_aux else set()
        direct_keys = primary_keys | aux_keys
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in direct_keys and isinstance(v, str):
                if cls._looks_like_non_text_value(v):
                    continue
                out.append(v)
            else:
                cls._collect_text_values(v, out, include_aux=include_aux)

    @staticmethod
    def _looks_like_non_text_value(value: str) -> bool:
        s = (value or "").strip()
        if not s:
            return True

        lower = s.lower()
        if lower.startswith(("http://", "https://", "file://", "data:image/")):
            return True

        path_markers = (
            "/users/",
            "\\users\\",
            "/appdata/",
            "\\appdata\\",
            "/sdk_storage/",
            "\\sdk_storage\\",
            "/resources/images/",
            "\\resources\\images\\",
        )
        if any(marker in lower for marker in path_markers):
            return True

        if re.search(r"(?:^|[\\/])img_v\d+_[^\\/\s]+\.(?:jpg|jpeg|png|webp|bmp)$", lower):
            return True

        if ("/" in s or "\\" in s) and re.search(r"\.(?:jpg|jpeg|png|webp|bmp|gif|svg|pdf|zip)$", lower):
            return True

        return False

    @classmethod
    def _collect_positioned_text_values(cls, obj: Any) -> list[tuple[str, tuple[float, float, float, float], int]]:
        out: list[tuple[str, tuple[float, float, float, float], int]] = []
        seq = {"i": 0}
        cls._walk_positioned_text_values(obj, out, seq)
        return out

    @classmethod
    def _walk_positioned_text_values(
        cls,
        obj: Any,
        out: list[tuple[str, tuple[float, float, float, float], int]],
        seq: dict[str, int],
    ) -> None:
        if obj is None:
            return
        if isinstance(obj, list):
            for it in obj:
                cls._walk_positioned_text_values(it, out, seq)
            return
        if not isinstance(obj, dict):
            return

        text = cls._first_text_in_dict(obj)
        bbox = cls._first_bbox_in_dict(obj)
        if text and bbox is not None:
            out.append((text, bbox, int(seq["i"])))
            seq["i"] += 1
            # Avoid parent+child duplicate extraction (e.g. line-level text + word-level text).
            return

        for v in obj.values():
            cls._walk_positioned_text_values(v, out, seq)

    @staticmethod
    def _first_text_in_dict(obj: dict[str, Any]) -> str:
        for k in ("text", "rec_text", "ocr_text", "transcription"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""

    @classmethod
    def _first_bbox_in_dict(cls, obj: dict[str, Any]) -> tuple[float, float, float, float] | None:
        for k in ("bbox", "box", "points", "polygon", "position", "quad", "text_region"):
            if k in obj:
                bb = cls._bbox_from_any(obj.get(k))
                if bb is not None:
                    return bb
        return None

    @classmethod
    def _bbox_from_any(cls, value: Any) -> tuple[float, float, float, float] | None:
        if value is None:
            return None

        if isinstance(value, dict):
            pairs = [
                ("x0", "y0", "x1", "y1"),
                ("left", "top", "right", "bottom"),
                ("xmin", "ymin", "xmax", "ymax"),
            ]
            for a, b, c, d in pairs:
                if all(key in value for key in (a, b, c, d)):
                    try:
                        x0 = float(value[a])
                        y0 = float(value[b])
                        x1 = float(value[c])
                        y1 = float(value[d])
                        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
                    except Exception:
                        continue
            return None

        if isinstance(value, (list, tuple)):
            vals = list(value)
            if len(vals) == 4 and all(isinstance(v, (int, float)) for v in vals):
                x0, y0, x1, y1 = (float(vals[0]), float(vals[1]), float(vals[2]), float(vals[3]))
                return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

            if len(vals) >= 8 and all(isinstance(v, (int, float)) for v in vals):
                xs = [float(vals[i]) for i in range(0, len(vals), 2)]
                ys = [float(vals[i]) for i in range(1, len(vals), 2)]
                return (min(xs), min(ys), max(xs), max(ys))

            points: list[tuple[float, float]] = []
            for it in vals:
                if isinstance(it, dict) and "x" in it and "y" in it:
                    try:
                        points.append((float(it["x"]), float(it["y"])))
                    except Exception:
                        pass
                elif (
                    isinstance(it, (list, tuple))
                    and len(it) >= 2
                    and isinstance(it[0], (int, float))
                    and isinstance(it[1], (int, float))
                ):
                    points.append((float(it[0]), float(it[1])))
            if points:
                xs = [p[0] for p in points]
                ys = [p[1] for p in points]
                return (min(xs), min(ys), max(xs), max(ys))

        return None

    @classmethod
    def _join_positioned_text_values(cls, items: list[tuple[str, tuple[float, float, float, float], int]]) -> str:
        heights = [max(1.0, bb[3] - bb[1]) for _, bb, _ in items]
        median_h = statistics.median(heights) if heights else 12.0
        line_tol = max(4.0, median_h * 0.6)

        sorted_items = sorted(items, key=lambda it: ((it[1][1] + it[1][3]) / 2.0, it[1][0], it[2]))
        out_parts: list[str] = []
        prev_text = ""
        prev_bb: tuple[float, float, float, float] | None = None

        for text, bb, _ in sorted_items:
            text = text.strip()
            if not text:
                continue

            if prev_bb is None:
                out_parts.append(text)
            else:
                prev_y = (prev_bb[1] + prev_bb[3]) / 2.0
                cur_y = (bb[1] + bb[3]) / 2.0
                new_line = abs(cur_y - prev_y) > line_tol
                if new_line:
                    out_parts.append("\n")
                    out_parts.append(text)
                else:
                    gap = bb[0] - prev_bb[2]
                    if gap > median_h * 0.35 and cls._needs_space(prev_text, text):
                        out_parts.append(" ")
                    out_parts.append(text)

            prev_text = text
            prev_bb = bb

        return "".join(out_parts).strip()

    @staticmethod
    def _needs_space(left: str, right: str) -> bool:
        if not left or not right:
            return False

        def _is_ascii_word_char(ch: str) -> bool:
            return ch.isascii() and (ch.isalnum() or ch in {"_", "-", "."})

        return _is_ascii_word_char(left[-1]) and _is_ascii_word_char(right[0])

    @classmethod
    def _find_first_value(cls, obj: Any, keys: set[str]) -> Any:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if str(k).lower() in keys:
                    return v
                found = cls._find_first_value(v, keys)
                if found is not None:
                    return found
        elif isinstance(obj, list):
            for it in obj:
                found = cls._find_first_value(it, keys)
                if found is not None:
                    return found
        return None
