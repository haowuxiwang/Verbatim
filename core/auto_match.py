"""Automated region matching algorithm (V1 / Enhancement).

This module provides intelligent heuristics to recommend region mappings
between PDF pages, reducing manual effort for users.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import rapidfuzz

from .models import BBox, CharData, RegionData


def _extract_text_content(region: RegionData) -> str:
    """Extract all text from a region, normalized."""
    return "".join(ch.char for ch in region.chars).strip()


def _calculate_text_similarity(text1: str, text2: str) -> float:
    """Calculate similarity score between two text strings using rapidfuzz."""
    if not text1 and not text2:
        return 1.0
    if not text1 or not text2:
        return 0.0

    # Use rapidfuzz for partial matching
    ratio = rapidfuzz.fuzz.ratio(text1, text2) / 100.0
    partial_ratio = rapidfuzz.fuzz.partial_ratio(text1, text2) / 100.0
    token_sort_ratio = rapidfuzz.fuzz.token_sort_ratio(text1, text2) / 100.0

    # Weighted average
    return 0.4 * ratio + 0.3 * partial_ratio + 0.3 * token_sort_ratio


def _extract_paragraphs(chars: list[CharData]) -> list[list[CharData]]:
    """Group characters into paragraphs based on spacing."""
    paragraphs: list[list[CharData]] = []
    current_paragraph: list[CharData] = []

    for char in chars:
        if char.char in ["\n", "\r", "\r\n"]:
            if current_paragraph:
                paragraphs.append(current_paragraph)
                current_paragraph = []
        else:
            current_paragraph.append(char)

    if current_paragraph:
        paragraphs.append(current_paragraph)

    return paragraphs


def _calculate_y_overlap(bbox1: BBox, bbox2: BBox) -> float:
    """Calculate vertical overlap ratio between two bounding boxes."""
    y0_1, y1_1 = bbox1[1], bbox1[3]
    y0_2, y1_2 = bbox2[1], bbox2[3]

    # Calculate overlap
    overlap_start = max(y0_1, y0_2)
    overlap_end = min(y1_1, y1_2)

    if overlap_start >= overlap_end:
        return 0.0

    overlap_height = overlap_end - overlap_start
    max_height = max(y1_1 - y0_1, y1_2 - y0_2)

    return overlap_height / max_height if max_height > 0 else 0.0


def _calculate_alignment_score(left_region: RegionData, right_region: RegionData) -> float:
    """Calculate how well two regions align based on text content and position."""
    # Text similarity
    text1 = _extract_text_content(left_region)
    text2 = _extract_text_content(right_region)
    text_score = _calculate_text_similarity(text1, text2)

    # Position alignment
    if left_region.bboxes and right_region.bboxes:
        # Calculate average y-overlap
        bbox_scores = []
        for left_bbox in left_region.bboxes:
            for right_bbox in right_region.bboxes:
                bbox_scores.append(_calculate_y_overlap(left_bbox, right_bbox))

        if bbox_scores:
            position_score = max(bbox_scores)  # Best match
        else:
            position_score = 0.0
    else:
        position_score = 0.0

    # Weighted combination
    return 0.7 * text_score + 0.3 * position_score


def _merge_close_bboxes(bboxes: list[BBox], threshold: float = 10.0) -> list[BBox]:
    """Merge bounding boxes that are close to each other."""
    if not bboxes:
        return []

    # Sort by x0 coordinate
    sorted_bboxes = sorted(bboxes, key=lambda b: b[0])

    merged = [sorted_bboxes[0]]
    for current in sorted_bboxes[1:]:
        last = merged[-1]

        # Check if boxes are close enough to merge
        if (current[0] - last[2]) <= threshold and abs(current[1] - last[1]) <= threshold:
            # Merge boxes
            new_bbox = (
                min(last[0], current[0]),
                min(last[1], current[1]),
                max(last[2], current[2]),
                max(last[3], current[3]),
            )
            merged[-1] = new_bbox
        else:
            merged.append(current)

    return merged


def find_candidate_regions(
    left_page_data: Any,  # PageData
    right_page_data: Any,  # PageData,
    left_regions: list[RegionData],
    right_regions: list[RegionData],
    threshold: float = 0.6,
) -> list[tuple[int, int, float]]:
    """Find candidate region mappings between left and right pages.

    Args:
        left_page_data: Complete left page data
        right_page_data: Complete right page data
        left_regions: List of regions on left page
        right_regions: List of regions on right page
        threshold: Minimum similarity score to consider

    Returns:
        List of (left_idx, right_idx, score) tuples sorted by score descending
    """

    candidates = []

    for i, left_region in enumerate(left_regions):
        for j, right_region in enumerate(right_regions):
            score = _calculate_alignment_score(left_region, right_region)
            if score >= threshold:
                candidates.append((i, j, score))

    # Sort by score descending
    candidates.sort(key=lambda x: x[2], reverse=True)
    return candidates


def suggest_mappings(
    left_page_data: Any, right_page_data: Any, max_candidates_per_region: int = 3
) -> list[tuple[int, int, float]]:
    """Suggest optimal region mappings between two pages.

    Uses a greedy algorithm to select best matches while avoiding conflicts.
    """

    # Build candidate regions from page structure first.
    left_regions = analyze_document_structure(left_page_data)
    right_regions = analyze_document_structure(right_page_data)

    # First, find all candidate region pairs.
    candidates = find_candidate_regions(left_page_data, right_page_data, left_regions, right_regions, threshold=0.5)

    # Select best mappings using greedy approach
    selected = []
    used_left = set()
    used_right = set()

    for left_idx, right_idx, score in candidates:
        if left_idx not in used_left and right_idx not in used_right:
            selected.append((left_idx, right_idx, score))
            used_left.add(left_idx)
            used_right.add(right_idx)

            # Stop if we have enough candidates
            if len(selected) >= max_candidates_per_region:
                break

    return selected


def create_regions_from_text_selection(
    page_data: Any, text_selection: str, fuzz_threshold: float = 0.8
) -> list[RegionData]:
    """Create regions from text selection by matching text in the page.

    Args:
        page_data: PageData containing all characters
        text_selection: Text to search for in the page
        fuzz_threshold: Similarity threshold for partial matches

    Returns:
        List of RegionData containing matching text
    """

    # Find all occurrences of the text (with fuzzy matching)
    matches = []

    # Split page into paragraphs for better matching
    paragraphs = _extract_paragraphs(page_data.text_chars)

    for paragraph in paragraphs:
        paragraph_text = "".join(ch.char for ch in paragraph)
        if len(paragraph_text) < len(text_selection) * 0.5:
            continue

        # Check for match
        similarity = rapidfuzz.fuzz.partial_ratio(text_selection, paragraph_text) / 100.0

        if similarity >= fuzz_threshold:
            # Create region from this paragraph
            region_chars = paragraph
            region_bboxes = _merge_close_bboxes([ch.bbox for ch in paragraph])

            region = RegionData(page_number=page_data.page_number, bboxes=region_bboxes, chars=region_chars)
            matches.append(region)

    return matches


def analyze_document_structure(page_data: Any) -> list[RegionData]:
    """Analyze document structure and identify natural regions (headings, paragraphs, etc.).

    This is a simple heuristic-based approach that can be improved later.
    """

    regions = []

    # Simple heuristic: group by paragraphs
    paragraphs = _extract_paragraphs(page_data.text_chars)

    for paragraph in paragraphs:
        if len(paragraph) < 5:  # Skip very short lines
            continue

        region_bboxes = _merge_close_bboxes([ch.bbox for ch in paragraph])

        region = RegionData(page_number=page_data.page_number, bboxes=region_bboxes, chars=paragraph)
        regions.append(region)

    return regions


def save_suggested_mappings(mappings: list[tuple[int, int, float]], filepath: Path) -> None:
    """Save suggested mappings to a JSON file."""
    data = {
        "type": "suggested_mappings",
        "suggestions": [
            {"left_index": left_idx, "right_index": right_idx, "score": score}
            for left_idx, right_idx, score in mappings
        ],
    }

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_suggested_mappings(filepath: Path) -> list[tuple[int, int, float]] | None:
    """Load suggested mappings from a JSON file."""
    try:
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)

        if data.get("type") != "suggested_mappings":
            return None

        return [(item["left_index"], item["right_index"], item["score"]) for item in data["suggestions"]]
    except Exception:
        return None


def main() -> int:
    """Simple test for the auto_match module."""
    from .models import CharData, StyleFlags

    # Create test data
    left_chars = [
        CharData("Hello", 0, (0, 0, 50, 20), "Arial", "Arial", 12.0, (0, 0, 0), StyleFlags()),
        CharData(" ", 1, (50, 0, 60, 20), "Arial", "Arial", 12.0, (0, 0, 0), StyleFlags()),
        CharData("World", 2, (60, 0, 110, 20), "Arial", "Arial", 12.0, (0, 0, 0), StyleFlags()),
    ]

    right_chars = [
        CharData("Hello", 10, (0, 0, 50, 20), "Arial", "Arial", 12.0, (0, 0, 0), StyleFlags()),
        CharData(" ", 11, (50, 0, 60, 20), "Arial", "Arial", 12.0, (0, 0, 0), StyleFlags()),
        CharData("World", 12, (60, 0, 110, 20), "Arial", "Arial", 12.0, (0, 0, 0), StyleFlags()),
    ]

    left_region = RegionData(0, [(0, 0, 110, 20)], left_chars)
    right_region = RegionData(0, [(0, 0, 110, 20)], right_chars)

    # Test similarity
    similarity = _calculate_alignment_score(left_region, right_region)
    print(f"Test similarity score: {similarity:.2f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
