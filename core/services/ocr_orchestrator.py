from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class OcrFallbackResult:
    left_text: str
    right_text: str
    left_ocr_applied: bool
    right_ocr_applied: bool
    ocr_used: bool
    ocr_note: str
    replaced_sides: list[str]
    skipped_no_config: bool
    attempted_but_empty: bool


def run_ocr_fallback(
    *,
    use_ocr: bool,
    has_ocr_config: bool,
    left_try_ocr: bool,
    right_try_ocr: bool,
    left_text: str,
    right_text: str,
    fetch_ocr_text: Callable[[str, str, str], str],
) -> OcrFallbackResult:
    if not use_ocr:
        return OcrFallbackResult(
            left_text=left_text,
            right_text=right_text,
            left_ocr_applied=False,
            right_ocr_applied=False,
            ocr_used=False,
            ocr_note="",
            replaced_sides=[],
            skipped_no_config=False,
            attempted_but_empty=False,
        )
    if not has_ocr_config:
        return OcrFallbackResult(
            left_text=left_text,
            right_text=right_text,
            left_ocr_applied=False,
            right_ocr_applied=False,
            ocr_used=False,
            ocr_note="",
            replaced_sides=[],
            skipped_no_config=True,
            attempted_but_empty=False,
        )
    if not (left_try_ocr or right_try_ocr):
        return OcrFallbackResult(
            left_text=left_text,
            right_text=right_text,
            left_ocr_applied=False,
            right_ocr_applied=False,
            ocr_used=False,
            ocr_note="",
            replaced_sides=[],
            skipped_no_config=False,
            attempted_but_empty=False,
        )

    left_ocr_text = left_text
    right_ocr_text = right_text
    replaced_sides: list[str] = []
    left_ocr_applied = False
    right_ocr_applied = False

    if left_try_ocr:
        t = fetch_ocr_text("left", left_text, right_text)
        if t:
            left_ocr_text = t
            replaced_sides.append("左侧")
            left_ocr_applied = True
    if right_try_ocr:
        t = fetch_ocr_text("right", right_text, left_text)
        if t:
            right_ocr_text = t
            replaced_sides.append("右侧")
            right_ocr_applied = True

    if not replaced_sides:
        return OcrFallbackResult(
            left_text=left_text,
            right_text=right_text,
            left_ocr_applied=False,
            right_ocr_applied=False,
            ocr_used=False,
            ocr_note="",
            replaced_sides=[],
            skipped_no_config=False,
            attempted_but_empty=True,
        )

    if left_ocr_applied and right_ocr_applied:
        note = f"已启用OCR文本回退（{','.join(replaced_sides)}，当前结果不含定位高亮）。"
    else:
        note = f"已启用OCR文本回退（{','.join(replaced_sides)}，仅非OCR侧支持定位高亮）。"

    return OcrFallbackResult(
        left_text=left_ocr_text,
        right_text=right_ocr_text,
        left_ocr_applied=left_ocr_applied,
        right_ocr_applied=right_ocr_applied,
        ocr_used=True,
        ocr_note=note,
        replaced_sides=replaced_sides,
        skipped_no_config=False,
        attempted_but_empty=False,
    )
