"""Format diff (V1 / Phase 5).

UI-free and PDF-free. Compares formatting changes between two RegionData objects.

Rules (frozen):
- size diff >= 0.5pt => change
- color diff: RGB Euclidean distance >= 15 => change
- font family differs => change
- bold/italic differs => change
- do NOT compare position changes

Output: list[DiffOp] where type == DiffOpType.FORMAT_CHANGE.
"""

from __future__ import annotations

import math
from typing import Any

from .models import RGB, CharData, DiffOp, DiffOpType, RegionData

SIZE_THRESHOLD_PT = 0.5
COLOR_DISTANCE_THRESHOLD = 15.0


def _rgb_distance(a: RGB, b: RGB) -> float:
    dr = float(a[0] - b[0])
    dg = float(a[1] - b[1])
    db = float(a[2] - b[2])
    return math.sqrt(dr * dr + dg * dg + db * db)


def _format_changes(left: CharData, right: CharData) -> dict[str, Any]:
    """Return a dict describing format changes for a matched character."""

    changes: dict[str, Any] = {}

    size_delta = abs(left.size - right.size)
    if size_delta >= SIZE_THRESHOLD_PT:
        changes["size"] = {"left": left.size, "right": right.size, "delta": size_delta}

    if left.font_family != right.font_family:
        changes["font_family"] = {"left": left.font_family, "right": right.font_family}

    if left.style.bold != right.style.bold:
        changes["bold"] = {"left": left.style.bold, "right": right.style.bold}

    if left.style.italic != right.style.italic:
        changes["italic"] = {"left": left.style.italic, "right": right.style.italic}

    color_delta = _rgb_distance(left.color_rgb, right.color_rgb)
    if color_delta >= COLOR_DISTANCE_THRESHOLD:
        changes["color_rgb"] = {
            "left": left.color_rgb,
            "right": right.color_rgb,
            "delta": color_delta,
        }

    return changes


def _lcs_match_pairs(a: list[str], b: list[str]) -> list[tuple[int, int]]:
    """Return increasing index pairs (i, j) for one LCS alignment of a/b."""

    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return []

    # If the text is too long, use greedy approximation
    if n > 1000 or m > 1000:
        return _greedy_char_pairs(a, b)

    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        ai = a[i]
        row = dp[i]
        row_next = dp[i + 1]
        for j in range(m - 1, -1, -1):
            if ai == b[j]:
                row[j] = 1 + row_next[j + 1]
            else:
                v1 = row_next[j]
                v2 = row[j + 1]
                row[j] = v1 if v1 >= v2 else v2

    pairs: list[tuple[int, int]] = []
    i = j = 0
    while i < n and j < m:
        if a[i] == b[j]:
            pairs.append((i, j))
            i += 1
            j += 1
        else:
            if dp[i + 1][j] >= dp[i][j + 1]:
                i += 1
            else:
                j += 1
    return pairs


def format_diff_regions(left: RegionData, right: RegionData) -> list[DiffOp]:
    """Compute FORMAT_CHANGE ops between two regions.

    Notes:
    - This function does NOT emit add/del/replace; it only reports formatting changes
      on characters aligned by an LCS over `CharData.char`.
    - Indices in DiffOp are `CharData.index` values from the corresponding page.
    """

    left_chars = left.chars
    right_chars = right.chars
    left_seq = [c.char for c in left_chars]
    right_seq = [c.char for c in right_chars]

    pairs = _lcs_match_pairs(left_seq, right_seq)

    changed: list[tuple[int, int, dict[str, Any]]] = []
    for li, ri in pairs:
        lch = left_chars[li]
        rch = right_chars[ri]
        c = _format_changes(lch, rch)
        if c:
            changed.append((li, ri, c))

    if not changed:
        return []

    # Group consecutive changes (by sequence position, not by CharData.index).
    ops: list[DiffOp] = []
    run: list[tuple[int, int, dict[str, Any]]] = []

    def flush_run() -> None:
        nonlocal run
        if not run:
            return
        left_indices = [left_chars[li].index for li, _, _ in run]
        right_indices = [right_chars[ri].index for _, ri, _ in run]
        left_bboxes = [left_chars[li].bbox for li, _, _ in run]
        right_bboxes = [right_chars[ri].bbox for _, ri, _ in run]

        reason_keys: set[str] = set()
        details: list[dict[str, Any]] = []
        for li, ri, c in run:
            reason_keys.update(c.keys())
            details.append(
                {
                    "char": left_chars[li].char,
                    "left_index": left_chars[li].index,
                    "right_index": right_chars[ri].index,
                    "changes": c,
                }
            )

        ops.append(
            DiffOp(
                type=DiffOpType.FORMAT_CHANGE,
                left_indices=left_indices,
                right_indices=right_indices,
                left_bboxes=left_bboxes,
                right_bboxes=right_bboxes,
                meta={
                    "reasons": sorted(reason_keys),
                    "details": details,
                },
            )
        )
        run = []

    prev_li = prev_ri = None
    for li, ri, c in changed:
        if prev_li is None:
            run.append((li, ri, c))
        else:
            assert prev_ri is not None
            contiguous = (li == prev_li + 1) and (ri == prev_ri + 1)
            if contiguous:
                run.append((li, ri, c))
            else:
                flush_run()
                run.append((li, ri, c))
        prev_li, prev_ri = li, ri

    flush_run()
    return ops


def _greedy_char_pairs(a: list[str], b: list[str]) -> list[tuple[int, int]]:
    """Greedy matching for large character sequences (O(n+m) complexity)."""
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return []

    pairs: list[tuple[int, int]] = []
    i = j = 0

    # Try to match common prefixes first
    while i < n and j < m and a[i] == b[j]:
        pairs.append((i, j))
        i += 1
        j += 1

    # For the rest, use a greedy approach
    while i < n and j < m:
        # Look for a match in a small window
        window_size = min(10, max(n - i, m - j) // 10)
        found = False

        for k in range(window_size):
            if i + k < n and j + k < m and a[i + k] == b[j + k]:
                pairs.append((i + k, j + k))
                i += k + 1
                j += k + 1
                found = True
                break

        if not found:
            i += 1
            j += 1

    return pairs
