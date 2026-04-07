"""Verbatim core data models (Phase 1 / V1).

Only defines the foundational structures requested for Phase 1:
- PageData
- CharData
- RegionData
- DiffOp

Project constraints (for later phases):
- Text diff is character-level; spaces are significant; consecutive newlines normalize to one space.
- Format diff uses frozen thresholds (size/font family/bold/italic/color RGB Δ).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

BBox = tuple[float, float, float, float]  # (x0, y0, x1, y1)
RGB = tuple[int, int, int]  # (r, g, b) each 0..255


@dataclass(frozen=True, slots=True)
class StyleFlags:
    bold: bool = False
    italic: bool = False


@dataclass(frozen=True, slots=True)
class CharData:
    """A single character (glyph) with formatting & location."""

    char: str
    index: int  # stable per-page index in reading order
    bbox: BBox
    font_name: str
    font_family: str
    size: float  # pt
    color_rgb: RGB
    style: StyleFlags = field(default_factory=StyleFlags)


@dataclass(frozen=True, slots=True)
class PageData:
    file_path: str
    page_number: int  # 0-based
    width: float
    height: float
    text_chars: list[CharData]


@dataclass(frozen=True, slots=True)
class RegionData:
    """A region selection on a single page.

    For multi-rect selections (N→1 or 1→N mapping sides), `bboxes` contains N rectangles.
    `chars` is the flattened character list extracted from those rectangles.
    """

    page_number: int
    bboxes: list[BBox]
    chars: list[CharData]


class DiffOpType(str, Enum):
    ADD = "add"
    DEL = "del"
    REPLACE = "replace"
    VISUAL_DIFF = "visual_diff"
    FORMAT_CHANGE = "format_change"


@dataclass(frozen=True, slots=True)
class DiffOp:
    type: DiffOpType
    left_indices: list[int]
    right_indices: list[int]
    left_bboxes: list[BBox]
    right_bboxes: list[BBox]
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-friendly representation.

        Note: bboxes are tuples but `json.dumps` will serialize them as lists.
        """

        return {
            "type": self.type.value,
            "left_indices": list(self.left_indices),
            "right_indices": list(self.right_indices),
            "left_bboxes": list(self.left_bboxes),
            "right_bboxes": list(self.right_bboxes),
            "meta": self.meta,
        }
