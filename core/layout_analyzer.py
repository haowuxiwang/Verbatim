from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .models import CharData


class LayoutType(Enum):
    SINGLE_COLUMN = "single"
    TWO_COLUMN = "two_column"
    MULTI_COLUMN = "multi_column"


@dataclass
class LayoutInfo:
    layout_type: LayoutType
    column_count: int
    column_boundaries: list[float]
    confidence: float


@dataclass
class WordInfo:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    char_indices: list[int]


@dataclass
class _ColumnCluster:
    center: float
    count: int
    x0: float
    x1: float


def _kmeans_1d(values: list[float], k: int = 2, max_iter: int = 100) -> tuple[list[float], list[int]]:
    if not values:
        return [], []
    if k <= 1:
        mean = sum(values) / len(values)
        return [mean], [0] * len(values)

    min_val, max_val = min(values), max(values)
    if min_val == max_val:
        return [min_val], [0] * len(values)

    centers = [min_val + (max_val - min_val) * i / (k - 1) for i in range(k)]
    labels = [0] * len(values)

    for _ in range(max_iter):
        new_labels = []
        for v in values:
            distances = [abs(v - c) for c in centers]
            new_labels.append(distances.index(min(distances)))

        if new_labels == labels:
            break
        labels = new_labels

        new_centers = []
        for i in range(k):
            cluster_values = [values[j] for j in range(len(values)) if labels[j] == i]
            if cluster_values:
                new_centers.append(sum(cluster_values) / len(cluster_values))
            else:
                new_centers.append(centers[i])
        centers = new_centers

    return centers, labels


def _detect_two_column_clusters(chars: list[CharData]) -> tuple[_ColumnCluster, _ColumnCluster] | None:
    """Detect two-column structure using x-center clustering and overlap checks."""
    if len(chars) < 40:
        return None

    x_centers = [((c.bbox[0] + c.bbox[2]) / 2.0) for c in chars]
    centers, labels = _kmeans_1d(x_centers, k=2)
    if len(centers) < 2 or centers[0] == centers[1]:
        return None

    left_label, right_label = (0, 1) if centers[0] < centers[1] else (1, 0)

    def build_cluster(label: int, center: float) -> _ColumnCluster | None:
        sub = [ch for i, ch in enumerate(chars) if labels[i] == label]
        if not sub:
            return None
        return _ColumnCluster(
            center=float(center),
            count=len(sub),
            x0=min(ch.bbox[0] for ch in sub),
            x1=max(ch.bbox[2] for ch in sub),
        )

    left = build_cluster(left_label, centers[left_label])
    right = build_cluster(right_label, centers[right_label])
    if left is None or right is None:
        return None

    total = left.count + right.count
    if total == 0:
        return None

    left_ratio = left.count / total
    right_ratio = right.count / total
    if left_ratio < 0.18 or right_ratio < 0.18:
        return None

    left_width = max(left.x1 - left.x0, 1e-6)
    right_width = max(right.x1 - right.x0, 1e-6)
    overlap = max(0.0, min(left.x1, right.x1) - max(left.x0, right.x0))
    overlap_ratio = overlap / min(left_width, right_width)

    # Real two-column pages should not have heavy horizontal overlap.
    if overlap_ratio > 0.35:
        return None

    boundary = (left.center + right.center) / 2.0
    x_min = min(ch.bbox[0] for ch in chars)
    x_max = max(ch.bbox[2] for ch in chars)
    x_span = max(x_max - x_min, 1.0)
    boundary_window = x_span * 0.06
    in_boundary = 0
    for ch in chars:
        cx = (ch.bbox[0] + ch.bbox[2]) / 2.0
        if abs(cx - boundary) <= boundary_window:
            in_boundary += 1
    boundary_ratio = in_boundary / total

    # Two columns should have a visible gutter near the column boundary.
    if boundary_ratio > 0.16:
        return None

    return left, right


def detect_layout(chars: list[CharData], page_width: float) -> LayoutInfo:
    if len(chars) < 40:
        return LayoutInfo(LayoutType.SINGLE_COLUMN, 1, [], 1.0)

    clusters = _detect_two_column_clusters(chars)
    if clusters is None:
        return LayoutInfo(LayoutType.SINGLE_COLUMN, 1, [], 0.9)

    left, right = clusters
    boundary = (left.center + right.center) / 2.0
    distance_ratio = abs(right.center - left.center) / max(page_width, 1.0)
    confidence = max(0.0, min(1.0, distance_ratio))
    return LayoutInfo(LayoutType.TWO_COLUMN, 2, [boundary], confidence)


def _group_chars_into_lines(chars: list[CharData], line_threshold_factor: float) -> list[list[CharData]]:
    if not chars:
        return []

    heights = [(c.bbox[3] - c.bbox[1]) for c in chars if c.bbox[3] > c.bbox[1]]
    if heights:
        heights.sort()
        median_height = heights[len(heights) // 2]
        line_threshold = median_height * line_threshold_factor
    else:
        line_threshold = 5.0

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

    return lines


def _sort_single_column(chars: list[CharData], line_threshold_factor: float) -> list[CharData]:
    lines = _group_chars_into_lines(chars, line_threshold_factor)
    out: list[CharData] = []
    for line in lines:
        out.extend(sorted(line, key=lambda c: c.bbox[0]))
    return out


def _sort_two_column(chars: list[CharData], column_boundary: float, line_threshold_factor: float) -> list[CharData]:
    left_chars: list[CharData] = []
    right_chars: list[CharData] = []

    for ch in chars:
        center_x = (ch.bbox[0] + ch.bbox[2]) / 2.0
        if center_x < column_boundary:
            left_chars.append(ch)
        else:
            right_chars.append(ch)

    left_sorted = _sort_single_column(left_chars, line_threshold_factor)
    right_sorted = _sort_single_column(right_chars, line_threshold_factor)

    # Visual reading order for two-column pages.
    return left_sorted + right_sorted


def sort_chars_by_reading_order(
    chars: list[CharData],
    page_width: float,
    layout: LayoutInfo | None = None,
    line_threshold_factor: float = 0.7,
) -> list[CharData]:
    if len(chars) <= 1:
        return chars

    if layout is None:
        layout = detect_layout(chars, page_width)

    if layout.layout_type == LayoutType.TWO_COLUMN and layout.confidence >= 0.40:
        return _sort_two_column(chars, layout.column_boundaries[0], line_threshold_factor)
    return _sort_single_column(chars, line_threshold_factor)


def analyze_text_layer_quality(chars: list[CharData]) -> dict[str, Any]:
    if not chars:
        return {
            "quality": "empty",
            "issues": ["No text content in selection"],
            "punct_ratio": 0.0,
            "char_count": 0,
        }

    text = "".join(c.char for c in chars)
    char_count = len(text)

    punct_chars = "，。！？、；：‘’“”（）【】<>.,!?;:'\"()"
    punct_count = sum(1 for c in text if c in punct_chars)
    punct_ratio = punct_count / char_count if char_count else 0.0

    issues: list[str] = []
    if punct_ratio < 0.005:
        issues.append(f"Very low punctuation ratio ({punct_ratio:.1%}), possible OCR/scanned text")
    elif punct_ratio < 0.015:
        issues.append(f"Low punctuation ratio ({punct_ratio:.1%}), text layer may be incomplete")

    colon_count = text.count(":") + text.count("：")
    if colon_count < 2 and char_count > 50:
        issues.append("Few key-value separators detected, extraction quality may be unstable")

    return {
        "quality": "good" if not issues else "warning",
        "issues": issues,
        "punct_ratio": punct_ratio,
        "char_count": char_count,
    }
