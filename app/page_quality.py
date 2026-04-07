from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PageTextLayerStatus:
    force_ocr: bool
    brief_log: str = ""
    warning_banner: tuple[str, ...] = ()


def evaluate_page_text_layer(*, side_label: str, page_number: int, text_char_count: int) -> PageTextLayerStatus:
    if int(text_char_count) > 0:
        return PageTextLayerStatus(force_ocr=False)

    page_index = int(page_number)
    brief_log = f"[verbatim] Warning: {side_label} PDF page {page_index} has no text (may be scanned image)"
    banner = (
        f"\n{'=' * 60}",
        f"[!] 警告: {side_label} PDF 是扫描版，没有可用文本层。",
        "    已保留页面显示，可继续框选；对比时将优先尝试 OCR 回退。",
        f"{'=' * 60}\n",
    )
    return PageTextLayerStatus(force_ocr=True, brief_log=brief_log, warning_banner=banner)
