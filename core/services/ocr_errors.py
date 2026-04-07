from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OcrErrorInfo:
    code: str
    message: str


def classify_ocr_error(err: Exception | str) -> OcrErrorInfo:
    msg = str(err or "").strip()
    msg_lower = msg.lower()

    if isinstance(err, TimeoutError) or "timed out" in msg_lower or "timeout" in msg_lower or "exceeded" in msg_lower:
        return OcrErrorInfo(code="timeout", message=msg)

    if "empty file" in msg_lower or "空文件" in msg or "empty_text" in msg_lower:
        return OcrErrorInfo(code="empty_result", message=msg)

    if "ocr http 401" in msg_lower or "ocr http 403" in msg_lower:
        return OcrErrorInfo(code="auth", message=msg)
    if "unauthorized" in msg_lower or "forbidden" in msg_lower or "invalid token" in msg_lower:
        return OcrErrorInfo(code="auth", message=msg)

    if "missing ocr models" in msg_lower:
        return OcrErrorInfo(code="model", message=msg)
    if "model" in msg_lower and ("not found" in msg_lower or "missing" in msg_lower):
        return OcrErrorInfo(code="model", message=msg)

    if "paddleocr-json" in msg_lower or "paddleocr json" in msg_lower:
        return OcrErrorInfo(code="engine", message=msg)
    if "local ocr worker" in msg_lower or "worker" in msg_lower or "subprocess" in msg_lower:
        return OcrErrorInfo(code="worker", message=msg)

    if "offline runtime" in msg_lower or "runtime" in msg_lower or "paddleocr import failed" in msg_lower:
        return OcrErrorInfo(code="runtime", message=msg)

    if (
        "request failed" in msg_lower
        or "connection" in msg_lower
        or "network" in msg_lower
        or "ssl" in msg_lower
        or "certificate" in msg_lower
        or "handshake" in msg_lower
        or "proxy" in msg_lower
        or "dns" in msg_lower
    ):
        return OcrErrorInfo(code="network", message=msg)

    return OcrErrorInfo(code="unknown", message=msg)
