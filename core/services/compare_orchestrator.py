from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OcrDecision:
    left_try_ocr: bool
    right_try_ocr: bool
    left_reason: str
    right_reason: str

    @property
    def recommended(self) -> bool:
        return bool(self.left_try_ocr or self.right_try_ocr)


def collect_quality_warnings(
    *,
    left_doc_note: str,
    right_doc_note: str,
    left_quality: dict,
    right_quality: dict,
) -> tuple[list[str], dict[str, int]]:
    warnings: list[str] = []
    if left_doc_note:
        warnings.append(left_doc_note)
    if right_doc_note:
        warnings.append(right_doc_note)
    if left_quality.get("issues"):
        warnings.append(f"左侧(置信度{left_quality.get('confidence', 0)}): {', '.join(left_quality.get('issues', []))}")
    if right_quality.get("issues"):
        warnings.append(
            f"右侧(置信度{right_quality.get('confidence', 0)}): {', '.join(right_quality.get('issues', []))}"
        )

    scores = {
        "left": int(left_quality.get("confidence", 0)),
        "right": int(right_quality.get("confidence", 0)),
    }
    return warnings, scores


def decide_ocr(
    *,
    left_text: str,
    right_text: str,
    left_quality: dict,
    right_quality: dict,
    left_force_ocr: bool,
    right_force_ocr: bool,
    dual_ocr_linkage: bool,
    should_try_ocr_side,
) -> OcrDecision:
    left_try_ocr, left_reason = should_try_ocr_side(left_text, left_quality)
    right_try_ocr, right_reason = should_try_ocr_side(right_text, right_quality)

    allow_force_override = str(__import__("os").environ.get("VERBATIM_OCR_FORCE_ALLOW_TEXT", "1")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    force_min_chars = int(__import__("os").environ.get("VERBATIM_OCR_FORCE_MIN_CHARS", "80") or "80")
    if allow_force_override:
        if (
            left_force_ocr
            and left_quality.get("quality") == "good"
            and int(left_quality.get("char_count", 0)) >= force_min_chars
        ):
            left_force_ocr = False
            left_reason = f"{left_reason}; 文本层可用，已放弃文档级强制OCR"
        if (
            right_force_ocr
            and right_quality.get("quality") == "good"
            and int(right_quality.get("char_count", 0)) >= force_min_chars
        ):
            right_force_ocr = False
            right_reason = f"{right_reason}; 文本层可用，已放弃文档级强制OCR"

    if left_force_ocr:
        left_try_ocr = True
        left_reason = f"{left_reason}; 文档级策略=强制OCR"
    if right_force_ocr:
        right_try_ocr = True
        right_reason = f"{right_reason}; 文档级策略=强制OCR"

    if dual_ocr_linkage and (left_try_ocr or right_try_ocr):
        if not left_try_ocr:
            left_reason = f"{left_reason}; 双侧OCR联动"
        if not right_try_ocr:
            right_reason = f"{right_reason}; 双侧OCR联动"
        left_try_ocr = True
        right_try_ocr = True

    return OcrDecision(
        left_try_ocr=left_try_ocr,
        right_try_ocr=right_try_ocr,
        left_reason=left_reason,
        right_reason=right_reason,
    )


def build_compare_result_summary(
    *,
    left_ocr_applied: bool,
    right_ocr_applied: bool,
    dual_ocr_mode: bool,
    pure_content: bool,
    show_format_diffs: bool,
) -> str:
    ocr_summary = "未使用OCR"
    if left_ocr_applied and right_ocr_applied:
        ocr_summary = "OCR: 左右两侧"
    elif left_ocr_applied:
        ocr_summary = "OCR: 仅左侧"
    elif right_ocr_applied:
        ocr_summary = "OCR: 仅右侧"
    if dual_ocr_mode:
        ocr_summary = f"{ocr_summary}（双侧OCR模式）"
    pure_summary = "纯内容: 开" if pure_content else "纯内容: 关"
    format_summary = "格式差异: 已启用" if show_format_diffs else "格式差异: 已关闭"
    return f"{ocr_summary} | {pure_summary} | {format_summary}"
