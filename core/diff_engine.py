r"""Diff engine (V2 / Phase 4).

This module is UI-free and PDF-free.

Implements character-level diff with V2 text rules:
- Aggressive normalization: remove all invisible/whitespace干扰项
- Full-width/half-width unification
- Zero-width character removal
- Semantic similarity matching

Output is a structured list of DiffOp.

Example run:
  .\.venv\Scripts\python.exe -m core.diff_engine
"""

from __future__ import annotations

import logging
import re
import sys
import unicodedata
from dataclasses import asdict
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .models import DiffOp, DiffOpType

# Regex patterns
_NEWLINES_RE = re.compile(r"[\r\n]+")
_ALL_WHITESPACE_RE = re.compile(r"\s+")
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u200e\u200f\u2060\ufeff]")

# Full-width to half-width translation tables
_FULL_WIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_FULL_WIDTH_ALPHA_LOWER = str.maketrans(
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ", "abcdefghijklmnopqrstuvwxyz"
)
_FULL_WIDTH_ALPHA_UPPER = str.maketrans(
    "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
)

# Common punctuation normalization (full-width -> half-width)
_FULL_WIDTH_PUNCT = str.maketrans(
    {
        "，": ",",
        "。": ".",
        "：": ":",
        "；": ";",
        "！": "!",
        "？": "?",
        "（": "(",
        "）": ")",
        "【": "[",
        "】": "]",
        "｛": "{",
        "｝": "}",
        "＂": '"',
        "＇": "'",
        "＜": "<",
        "＞": ">",
        "／": "/",
        "＠": "@",
        "＃": "#",
        "％": "%",
        "＆": "&",
        "＊": "*",
        "＋": "+",
        "－": "-",
        "＝": "=",
        "＿": "_",
        "＼": "\\",
        "｜": "|",
        "～": "~",
        "｀": "`",
        "＾": "^",
        "　": " ",
    }
)

# Similar character mappings (common OCR/extraction errors)
_SIMILAR_CHARS = {
    "l": "i1",
    "I": "i1",
    "1": "il",
    "O": "o0",
    "0": "oO",
    "m": "rn",
    "rn": "m",
    "c": "C",
    "C": "c",
    "s": "S",
    "S": "s",
    "k": "K",
    "K": "k",
    "v": "V",
    "V": "v",
    "w": "W",
    "W": "w",
    "x": "X",
    "X": "x",
    "z": "Z",
    "Z": "z",
}


def _setup_rolling_file_logger() -> logging.Logger:
    """Create a local rolling-file logger (Phase 4 requirement)."""

    logger = logging.getLogger("verbatim")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "verbatim.log"

    handler = RotatingFileHandler(
        log_path,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)

    logger.propagate = False
    return logger


def normalize_text(
    text: str,
    *,
    aggressive: bool = True,
    remove_all_whitespace: bool = False,
    preserve_punctuation: bool = False,
    remove_punctuation: bool = False,
    normalize_numbers: bool = False,
    merge_key_value_lines: bool = False,
) -> str:
    """Normalize text for comparison.

    V2 normalization (aggressive mode):
    1. Unicode NFC normalization (combining characters)
    2. Remove zero-width characters (U+200B, U+FEFF, etc.)
    3. Normalize line endings (CRLF/CR -> LF -> space)
    4. Convert full-width to half-width (digits, letters, punctuation)
    5. Trim and collapse whitespace
    6. Optionally remove ALL whitespace for pure content comparison
    7. Optionally remove punctuation (v1.0 §3.1)
    8. Optionally normalize numbers - remove thousand separators (v1.0 §3.3)
    9. Optionally merge key-value lines (v1.0 §3.4)

    Args:
        text: Input text to normalize
        aggressive: If True, apply all normalizations (default)
                   If False, only normalize newlines (V1 behavior)
        remove_all_whitespace: If True, remove ALL whitespace characters
                              Useful for "pure content" comparison
        preserve_punctuation: If True, don't normalize full-width punctuation
                             Useful when punctuation differences matter
        remove_punctuation: If True, remove all punctuation for comparison (v1.0 §3.1)
        normalize_numbers: If True, remove thousand separators from numbers (v1.0 §3.3)
        merge_key_value_lines: If True, merge lines ending with colon (v1.0 §3.4)

    Returns:
        Normalized text string
    """
    if not text:
        return ""

    # Step 9: Merge key-value lines BEFORE other normalizations (v1.0 §3.4)
    if merge_key_value_lines:
        t = _merge_key_value_lines(text)
    else:
        t = text

    # Step 1: Unicode NFC normalization (canonical composition)
    t = unicodedata.normalize("NFC", t)

    # Step 2: Remove zero-width characters
    t = _ZERO_WIDTH_RE.sub("", t)

    # Step 3: Normalize line endings
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = _NEWLINES_RE.sub(" ", t)

    if not aggressive:
        return t

    # Step 4: Convert full-width to half-width
    t = t.translate(_FULL_WIDTH_DIGITS)
    t = t.translate(_FULL_WIDTH_ALPHA_LOWER)
    t = t.translate(_FULL_WIDTH_ALPHA_UPPER)

    # Only normalize punctuation if not preserving
    if not preserve_punctuation:
        t = t.translate(_FULL_WIDTH_PUNCT)

    # Step 8: Normalize numbers (remove thousand separators)
    if normalize_numbers:
        t = _normalize_numbers(t)

    # Step 7: Remove punctuation (v1.0 §3.1)
    if remove_punctuation:
        t = _remove_punctuation(t)

    # Step 5: Normalize whitespace
    t = t.strip()
    t = _ALL_WHITESPACE_RE.sub(" ", t)

    # Step 6: Optionally remove all whitespace
    if remove_all_whitespace:
        t = t.replace(" ", "")

    return t


def _merge_key_value_lines(text: str) -> str:
    """Merge lines that end with colon with the next line.

    v1.0 §3.4: 键值断行合并

    Rule: If a line ends with ":" or "：", merge it with the next line.
    Example:
        传真号码:
        0576-88827887
        →
        传真号码: 0576-88827887
    """
    lines = text.split("\n")
    merged_lines = []
    i = 0

    while i < len(lines):
        line = lines[i].rstrip()
        # Check if line ends with colon (half-width or full-width)
        if line.endswith(":") or line.endswith("："):
            # Try to merge with next line
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                # Don't merge if next line is empty or looks like a new header
                if next_line and not (
                    next_line.startswith("【")
                    or next_line.startswith("[")
                    or next_line.endswith(":")
                    or next_line.endswith("：")
                ):
                    merged_lines.append(line + " " + next_line)
                    i += 2
                    continue
        merged_lines.append(line)
        i += 1

    return "\n".join(merged_lines)


def _normalize_numbers(text: str) -> str:
    """Remove thousand separators from numbers.

    v1.0 §3.3: 数字标准化
    Example: 1,000 mg → 1000 mg
    """
    # Pattern: digit,digit,digit (thousand separator)
    # Must be careful not to break decimal numbers or other contexts
    import re

    # Match: digit(s) followed by comma followed by exactly 3 digits
    # This is a simplified implementation
    return re.sub(r"(\d),(\d{3})", r"\1\2", text)


def _remove_punctuation(text: str) -> str:
    """Remove all punctuation characters.

    v1.0 §3.1: 忽略标点差异
    """
    import re

    # Remove common punctuation (both Chinese and English)
    punctuation_pattern = r"[，。！？、；：\"'【】（）\[\]{}<>,.!?;:()\-—…·]"
    return re.sub(punctuation_pattern, "", text)


def chars_are_similar(a: str, b: str) -> bool:
    """Check if two characters are semantically similar.

    This handles common OCR/extraction errors and visual lookalikes:
    - Case differences (if needed)
    - l/I/1, O/0, m/rn confusion
    - etc.
    """
    if a == b:
        return True

    # Direct similarity check
    if a in _SIMILAR_CHARS and b in _SIMILAR_CHARS[a]:
        return True
    if b in _SIMILAR_CHARS and a in _SIMILAR_CHARS[b]:
        return True

    return False


def _lcs_match_pairs(a: str, b: str, use_similarity: bool = False) -> list[tuple[int, int]]:
    """Return increasing index pairs (i, j) that form one LCS alignment.

    Args:
        a: First string
        b: Second string
        use_similarity: If True, use semantic similarity for matching
                       (handles OCR errors like l/1, O/0)
    """

    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return []

    # If the text is too long, use a greedy approximation to avoid O(n*m) complexity
    if n > 1000 or m > 1000:
        return _greedy_match_pairs(a, b)

    # DP table of LCS lengths. Note: O(n*m) memory/time.
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        ai = a[i]
        row = dp[i]
        row_next = dp[i + 1]
        for j in range(m - 1, -1, -1):
            # Use exact match or similarity match
            if ai == b[j] or (use_similarity and chars_are_similar(ai, b[j])):
                row[j] = 1 + row_next[j + 1]
            else:
                v1 = row_next[j]
                v2 = row[j + 1]
                row[j] = v1 if v1 >= v2 else v2

    # Backtrack to recover matched pairs.
    pairs: list[tuple[int, int]] = []
    i = j = 0
    while i < n and j < m:
        if a[i] == b[j] or (use_similarity and chars_are_similar(a[i], b[j])):
            pairs.append((i, j))
            i += 1
            j += 1
        else:
            if dp[i + 1][j] >= dp[i][j + 1]:
                i += 1
            else:
                j += 1
    return pairs


def diff_text(
    left: str,
    right: str,
    *,
    pure_content_mode: bool = False,
    remove_punctuation: bool = False,
    normalize_numbers: bool = False,
    merge_key_value_lines: bool = False,
) -> list[DiffOp]:
    """Compute char-level diff ops between two strings.

    Args:
        left: Left text string
        right: Right text string
        pure_content_mode: If True, remove ALL whitespace before comparison.
                          This enables "pure content" comparison that ignores
                          spacing differences entirely.
        remove_punctuation: v1.0 §3.1 - Remove all punctuation
        normalize_numbers: v1.0 §3.3 - Normalize numbers (remove thousand separators)
        merge_key_value_lines: v1.0 §3.4 - Merge key-value lines

    Returns:
        List of DiffOp representing the differences
    """

    logger = _setup_rolling_file_logger()

    # Normalize with v1.0 options
    a = normalize_text(
        left,
        aggressive=True,
        remove_all_whitespace=pure_content_mode,
        remove_punctuation=remove_punctuation,
        normalize_numbers=normalize_numbers,
        merge_key_value_lines=merge_key_value_lines,
    )
    b = normalize_text(
        right,
        aggressive=True,
        remove_all_whitespace=pure_content_mode,
        remove_punctuation=remove_punctuation,
        normalize_numbers=normalize_numbers,
        merge_key_value_lines=merge_key_value_lines,
    )

    logger.info("diff_text: left_len=%s right_len=%s (pure_content=%s)", len(a), len(b), pure_content_mode)

    # If both normalized strings are identical, no diff needed
    if a == b:
        logger.info("diff_text: texts are identical after normalization")
        return []

    matches = _lcs_match_pairs(a, b, use_similarity=False)
    matches.append((len(a), len(b)))  # sentinel

    ops: list[DiffOp] = []
    ai = bi = 0
    for mi, mj in matches:
        left_seg = a[ai:mi]
        right_seg = b[bi:mj]

        if left_seg or right_seg:
            left_indices = list(range(ai, mi))
            right_indices = list(range(bi, mj))

            if left_seg and not right_seg:
                op_type = DiffOpType.DEL
            elif right_seg and not left_seg:
                op_type = DiffOpType.ADD
            else:
                op_type = DiffOpType.REPLACE

            ops.append(
                DiffOp(
                    type=op_type,
                    left_indices=left_indices,
                    right_indices=right_indices,
                    left_bboxes=[],
                    right_bboxes=[],
                    meta={"left_text": left_seg, "right_text": right_seg},
                )
            )

        # Skip the matched char (if not sentinel).
        if mi < len(a) and mj < len(b):
            ai = mi + 1
            bi = mj + 1
        else:
            ai = mi
            bi = mj

    logger.info("diff_text: ops=%s", len(ops))
    return ops


def _op_to_dict(op: DiffOp) -> dict:
    d = asdict(op)
    d["type"] = op.type.value
    return d


def _greedy_match_pairs(a: str, b: str, use_similarity: bool = False) -> list[tuple[int, int]]:
    """Greedy matching for large texts (O(n+m) complexity).

    Args:
        a: First string
        b: Second string
        use_similarity: If True, use semantic similarity for matching
    """
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return []

    pairs: list[tuple[int, int]] = []
    i = j = 0

    def match_char(ca: str, cb: str) -> bool:
        """Check if two characters match (exact or similar)."""
        if ca == cb:
            return True
        if use_similarity and chars_are_similar(ca, cb):
            return True
        return False

    # Try to match common prefixes first
    while i < n and j < m and match_char(a[i], b[j]):
        pairs.append((i, j))
        i += 1
        j += 1

    # For the rest, use a greedy approach
    while i < n and j < m:
        # Look for a match in a window
        window_size = min(10, max(n - i, m - j) // 10)
        found = False

        for k in range(window_size):
            if i + k < n and j + k < m and match_char(a[i + k], b[j + k]):
                pairs.append((i + k, j + k))
                i += k + 1
                j += k + 1
                found = True
                break

        if not found:
            i += 1
            j += 1

    return pairs


def main(argv: list[str]) -> int:
    # Manual, PDF-free smoke tests
    cases = [
        ("Hello\nWorld", "Hello World"),  # newline normalized
        ("a b", "ab"),  # space difference
        ("ABC", "AXBC"),  # insertion
        ("kitten", "sitting"),  # multiple changes
        ("line1\n\n\nline2", "line1 line2"),  # multiple newlines
        # V2 normalization tests
        ("电话：４００１１８０６１８", "电话:4001180618"),  # full-width -> half-width
        ("企业　名称", "企业 名称"),  # full-width space -> regular space
        ("www.hisunpharm.com", "www.hisunpharm.com"),  # identical URLs
        ("test\u200b\u200c", "test"),  # zero-width chars removed
    ]

    print("=" * 60)
    print("NORMALIZATION TESTS")
    print("=" * 60)

    for i, (l, r) in enumerate(cases, start=1):
        print(f"\nCASE {i}:")
        print(f"  Left:  {repr(l)}")
        print(f"  Right: {repr(r)}")

        # Show normalized versions
        norm_l = normalize_text(l)
        norm_r = normalize_text(r)
        print(f"  Norm L: {repr(norm_l)}")
        print(f"  Norm R: {repr(norm_r)}")

        ops = diff_text(l, r)
        if ops:
            print(f"  Diffs: {len(ops)}")
            for op in ops[:3]:  # Show first 3 diffs
                print(
                    f"    - {op.type.value}: {repr(op.meta.get('left_text', ''))} -> {repr(op.meta.get('right_text', ''))}"
                )
        else:
            print("  Diffs: NONE (texts match after normalization)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
