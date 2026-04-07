"""Region extraction utilities.

Extracts characters inside user-selected bounding boxes and returns them in a
stable reading order. Supports strict boundary filtering to prevent leakage.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable

from .layout_analyzer import LayoutInfo, LayoutType, sort_chars_by_reading_order
from .models import BBox, CharData, PageData, RegionData
from .pdf_parser import parse_page


def _normalize_bbox(b: BBox) -> BBox:
    x0, y0, x1, y1 = (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
    if x0 > x1:
        x0, x1 = x1, x0
    if y0 > y1:
        y0, y1 = y1, y0
    return (x0, y0, x1, y1)


def _center_in(a: BBox, b: BBox) -> bool:
    ax0, ay0, ax1, ay1 = _normalize_bbox(a)
    bx0, by0, bx1, by1 = _normalize_bbox(b)
    cx = (ax0 + ax1) / 2.0
    cy = (ay0 + ay1) / 2.0
    return (bx0 <= cx <= bx1) and (by0 <= cy <= by1)


def _bboxes_intersect(a: BBox, b: BBox) -> bool:
    ax0, ay0, ax1, ay1 = _normalize_bbox(a)
    bx0, by0, bx1, by1 = _normalize_bbox(b)
    return not (ax1 < bx0 or ax0 > bx1 or ay1 < by0 or ay0 > by1)


def _bbox_strictly_within(a: BBox, b: BBox, tolerance: float = 2.0) -> bool:
    ax0, ay0, ax1, ay1 = _normalize_bbox(a)
    bx0, by0, bx1, by1 = _normalize_bbox(b)
    return ax0 >= bx0 - tolerance and ax1 <= bx1 + tolerance and ay0 >= by0 - tolerance and ay1 <= by1 + tolerance


def _sort_chars_by_position(chars: list[CharData], line_threshold: float = 2.0) -> list[CharData]:
    if len(chars) <= 1:
        return chars

    sorted_by_y = sorted(chars, key=lambda c: (c.bbox[1], c.bbox[0]))
    lines: list[list[CharData]] = []
    current_line: list[CharData] = []
    current_y: float | None = None

    for ch in sorted_by_y:
        ch_y = ch.bbox[1]
        if current_y is None:
            current_y = ch_y
            current_line = [ch]
        elif abs(ch_y - current_y) <= line_threshold:
            current_line.append(ch)
            current_y = (current_y * (len(current_line) - 1) + ch_y) / len(current_line)
        else:
            lines.append(current_line)
            current_line = [ch]
            current_y = ch_y

    if current_line:
        lines.append(current_line)

    out: list[CharData] = []
    for line in lines:
        out.extend(sorted(line, key=lambda c: c.bbox[0]))
    return out


def extract_region(
    page_data: PageData,
    bboxes: list[BBox],
    *,
    use_intersection: bool = True,
    sort_by_position: bool = True,
    strict_bounds: bool = False,
    use_layout_analysis: bool = True,
    reading_order_mode: str = "auto",
) -> RegionData:
    nbs = [_normalize_bbox(b) for b in (bboxes or [])]
    if not nbs:
        return RegionData(page_number=page_data.page_number, bboxes=[], chars=[])

    kept: list[CharData] = []
    for ch in page_data.text_chars:
        if use_intersection:
            selected = any(_bboxes_intersect(ch.bbox, nb) for nb in nbs)
        else:
            selected = any(_center_in(ch.bbox, nb) for nb in nbs)

        if selected and strict_bounds:
            selected = any(_bbox_strictly_within(ch.bbox, nb) for nb in nbs)

        if selected:
            kept.append(ch)

    if sort_by_position and kept:
        if reading_order_mode == "raw":
            return RegionData(page_number=page_data.page_number, bboxes=nbs, chars=kept)

        if use_layout_analysis:
            try:
                page_width = float(page_data.width)
                region_width = max(nb[2] - nb[0] for nb in nbs) if nbs else page_width
                region_ratio = (region_width / page_width) if page_width > 0 else 1.0

                forced_layout: LayoutInfo | None = None
                if reading_order_mode == "single_column":
                    forced_layout = LayoutInfo(
                        layout_type=LayoutType.SINGLE_COLUMN,
                        column_count=1,
                        column_boundaries=[],
                        confidence=1.0,
                    )
                elif reading_order_mode == "two_column":
                    boundary = sum(nb[0] + nb[2] for nb in nbs) / (2.0 * len(nbs))
                    forced_layout = LayoutInfo(
                        layout_type=LayoutType.TWO_COLUMN,
                        column_count=2,
                        column_boundaries=[boundary],
                        confidence=1.0,
                    )
                elif region_ratio < 0.60:
                    # Narrow user selections are usually one paragraph in one column.
                    forced_layout = LayoutInfo(
                        layout_type=LayoutType.SINGLE_COLUMN,
                        column_count=1,
                        column_boundaries=[],
                        confidence=1.0,
                    )

                kept = sort_chars_by_reading_order(kept, page_width, layout=forced_layout)
            except Exception:
                kept = _sort_chars_by_position(kept)
        else:
            kept = _sort_chars_by_position(kept)

    return RegionData(page_number=page_data.page_number, bboxes=nbs, chars=kept)


def _region_text(chars: Iterable[CharData]) -> str:
    return "".join(c.char for c in chars)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("Usage: python -m core.region_extractor <pdf_path> [page_number]")
        return 2

    pdf_path = argv[1]
    page_number = int(argv[2]) if len(argv) >= 3 else 0

    page = parse_page(pdf_path, page_number)
    bbox: BBox = (200.0, 110.0, 420.0, 170.0)
    region = extract_region(page, [bbox])
    print(_region_text(region.chars))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
