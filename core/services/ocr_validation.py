from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path

from core.models import BBox
from core.services.ocr_models import OcrResult, OcrSpan
from core.services.ocr_validation_models import (
    OcrValidatedResult,
    OcrValidationIssue,
    OcrValidationSeverity,
    OcrValidationStatus,
)


def summarize_text(text: str) -> dict[str, int | str]:
    t = (text or "").strip()
    if not t:
        return {"length": 0, "lines": 0, "sha256_8": ""}
    h = hashlib.sha256(t.encode("utf-8", errors="ignore")).hexdigest()[:8]
    lines = len([ln for ln in t.splitlines() if ln.strip()])
    return {"length": len(t), "lines": lines, "sha256_8": h}


def validate_ocr_input(
    *,
    pdf_path: Path,
    page_number: int,
    bbox: BBox,
    image_bytes: bytes | None,
    clip_bbox: BBox | None,
    zoom: float,
) -> list[str]:
    issues: list[str] = []
    if not pdf_path or not Path(pdf_path).exists():
        issues.append("pdf_missing")
    if page_number < 0:
        issues.append("page_negative")
    x0, y0, x1, y1 = [float(v) for v in bbox]
    if x1 <= x0 or y1 <= y0:
        issues.append("bbox_invalid")
    if (x1 - x0) < 2.0 or (y1 - y0) < 2.0:
        issues.append("bbox_too_small")
    if zoom <= 0:
        issues.append("zoom_invalid")
    if image_bytes is None or len(image_bytes) < 64:
        issues.append("image_empty")
    if clip_bbox is not None:
        cx0, cy0, cx1, cy1 = [float(v) for v in clip_bbox]
        if cx1 <= cx0 or cy1 <= cy0:
            issues.append("clip_invalid")
        if (cx1 - cx0) < 2.0 or (cy1 - cy0) < 2.0:
            issues.append("clip_too_small")
    return issues


def validate_ocr_result(
    result: OcrResult,
    *,
    baseline_text: str,
    peer_text: str,
) -> OcrValidatedResult:
    issues: list[OcrValidationIssue] = []
    t = (result.text or "").strip()
    if not t:
        issues.append(OcrValidationIssue(code="text_empty", severity=OcrValidationSeverity.ERROR))
        return OcrValidatedResult(result=result, status=OcrValidationStatus(tuple(issues)))
    if len(t) < 2:
        issues.append(OcrValidationIssue(code="text_too_short", severity=OcrValidationSeverity.WARNING))
    if baseline_text and t == baseline_text.strip():
        issues.append(OcrValidationIssue(code="text_same_as_baseline", severity=OcrValidationSeverity.INFO))
    if peer_text and t == peer_text.strip():
        issues.append(OcrValidationIssue(code="text_same_as_peer", severity=OcrValidationSeverity.INFO))
    if result.spans is None:
        issues.append(OcrValidationIssue(code="spans_missing", severity=OcrValidationSeverity.WARNING))
    elif not result.spans:
        issues.append(OcrValidationIssue(code="spans_empty", severity=OcrValidationSeverity.WARNING))
    return OcrValidatedResult(result=result, status=OcrValidationStatus(tuple(issues)))


def validate_ocr_spans(
    *,
    spans: Iterable[OcrSpan] | None,
    clip_bbox: BBox | None,
) -> list[str]:
    issues: list[str] = []
    if spans is None:
        issues.append("spans_missing")
        return issues
    spans_list = list(spans)
    if not spans_list:
        issues.append("spans_empty")
        return issues
    if clip_bbox is None:
        return issues
    cx0, cy0, cx1, cy1 = [float(v) for v in clip_bbox]
    for span in spans_list:
        x0, y0, x1, y1 = [float(v) for v in span.bbox]
        if x1 <= x0 or y1 <= y0:
            issues.append("span_invalid")
            break
        if x0 < cx0 - 2 or y0 < cy0 - 2 or x1 > cx1 + 2 or y1 > cy1 + 2:
            issues.append("span_outside_clip")
            break
    return issues
