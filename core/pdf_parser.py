r"""PDF parsing (V1 / Phase 2).

UI-free and diff-free. Exposes `parse_page(file_path, page_number)`.

Example (sample asset `samples/manual-verification/original.pdf`, page 1 == page_number=0):
  .\.venv\Scripts\python.exe -m core.pdf_parser samples/manual-verification/original.pdf 0
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF

from .models import RGB, BBox, CharData, PageData, StyleFlags


def _color_to_rgb(color: Any) -> RGB:
    # In PyMuPDF rawdict, span['color'] is typically an int 0xRRGGBB.
    if isinstance(color, int):
        r = (color >> 16) & 255
        g = (color >> 8) & 255
        b = color & 255
        return (int(r), int(g), int(b))

    if isinstance(color, (list, tuple)) and len(color) == 3:
        return (int(color[0]), int(color[1]), int(color[2]))

    # Some PDFs / versions may expose grayscale or other compact representations.
    if isinstance(color, (list, tuple)) and len(color) == 2:
        try:
            v = int(color[0])
        except Exception:
            v = 0
        return (v, v, v)

    return (0, 0, 0)


def _normalize_font_family(font_name: str) -> str:
    """Remove subset prefixes like 'ABCDEE+FontName'."""
    if "+" in font_name:
        prefix, rest = font_name.split("+", 1)
        if len(prefix) == 6 and prefix.isalnum():
            return rest
    return font_name


def _fix_font_mojibake(font_name: str) -> str:
    """Best-effort fix for common Windows/GBK mojibake in embedded font names.

    Example: "ËÎÌå" (latin-1 decoded bytes) -> "宋体" (GBK).
    """

    if not font_name:
        return font_name
    # Only attempt when extended latin characters exist.
    if not any("\u00c0" <= ch <= "\u00ff" for ch in font_name):
        return font_name
    try:
        b = font_name.encode("latin1")
        decoded = b.decode("gbk")
        if any("\u4e00" <= ch <= "\u9fff" for ch in decoded):
            return decoded
    except Exception:
        return font_name
    return font_name


def _infer_style(font_name: str, flags: Any) -> StyleFlags:
    bold = False
    italic = False

    try:
        f = int(flags)
    except Exception:
        f = 0

    bold = bool(f & fitz.TEXT_FONT_BOLD)
    italic = bool(f & fitz.TEXT_FONT_ITALIC)

    # Fallback heuristics (some PDFs omit reliable flags)
    u = font_name.upper()
    if "BOLD" in u:
        bold = True
    if "ITALIC" in u or "OBLIQUE" in u:
        italic = True

    return StyleFlags(bold=bold, italic=italic)


def parse_page(file_path: str | Path, page_number: int) -> PageData:
    """Parse one page (0-based) into PageData with per-character CharData."""
    pdf_path = str(Path(file_path))

    with fitz.open(pdf_path) as doc:
        if page_number < 0 or page_number >= doc.page_count:
            raise IndexError(f"page_number out of range: {page_number} (0..{doc.page_count - 1})")
        page = doc.load_page(page_number)
        raw = page.get_text("rawdict")

        chars: list[CharData] = []
        idx = 0

        for block in raw.get("blocks", []):
            if block.get("type") != 0:
                continue  # non-text block
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    font_name = _fix_font_mojibake(str(span.get("font", "")))
                    font_family = _normalize_font_family(font_name)
                    size = float(span.get("size", 0.0))
                    color_rgb = _color_to_rgb(span.get("color", 0))
                    style = _infer_style(font_name, span.get("flags"))

                    for ch in span.get("chars", []) or []:
                        c_any = ch.get("c", "")
                        if isinstance(c_any, int):
                            c = chr(c_any)
                        else:
                            c = str(c_any)
                        bbox_list = ch.get("bbox", [0, 0, 0, 0])
                        bbox: BBox = (
                            float(bbox_list[0]),
                            float(bbox_list[1]),
                            float(bbox_list[2]),
                            float(bbox_list[3]),
                        )
                        chars.append(
                            CharData(
                                char=c,
                                index=idx,
                                bbox=bbox,
                                font_name=font_name,
                                font_family=font_family,
                                size=size,
                                color_rgb=color_rgb,
                                style=style,
                            )
                        )
                        idx += 1

        rect = page.rect
        return PageData(
            file_path=pdf_path,
            page_number=page_number,
            width=float(rect.width),
            height=float(rect.height),
            text_chars=chars,
        )


def _char_preview(ch: CharData) -> dict[str, Any]:
    rgb_any: Any = ch.color_rgb
    rgb: RGB
    if isinstance(rgb_any, (list, tuple)) and len(rgb_any) == 3:
        rgb = (int(rgb_any[0]), int(rgb_any[1]), int(rgb_any[2]))
    else:
        rgb = (0, 0, 0)
    return {
        "char": ch.char,
        "index": ch.index,
        "bbox": list(ch.bbox),
        "font": ch.font_name,
        "size": ch.size,
        "color": [rgb[0], rgb[1], rgb[2]],
        "bold": ch.style.bold,
        "italic": ch.style.italic,
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python -m core.pdf_parser <pdf_path> [page_number]")
        return 2

    pdf_path = argv[1]
    page_number = int(argv[2]) if len(argv) >= 3 else 0

    page = parse_page(pdf_path, page_number)
    preview = [_char_preview(c) for c in page.text_chars[:50]]
    # Use ASCII-only JSON to avoid Windows console encoding issues.
    # Print as a single line to reduce the chance of console/prompt noise interleaving.
    print(json.dumps(preview, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
