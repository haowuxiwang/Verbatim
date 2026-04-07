from __future__ import annotations

import json
import unicodedata
from dataclasses import asdict
from difflib import SequenceMatcher

from .diff_engine import _lcs_match_pairs, normalize_text
from .format_diff import format_diff_regions
from .models import CharData, DiffOp, DiffOpType, RegionData

_ZERO_WIDTH = {"\u200b", "\u200c", "\u200d", "\u200e", "\u200f", "\u2060", "\ufeff"}


class NormalizationLog:
    """Records applied normalization steps for user-visible audit log."""

    def __init__(self) -> None:
        self.steps: list[tuple[str, bool]] = []

    def add(self, step: str, enabled: bool) -> None:
        self.steps.append((step, enabled))

    def to_string(self) -> str:
        lines = ["已执行标准化步骤："]
        for step, enabled in self.steps:
            lines.append(f"{'✓' if enabled else '○'} {step}")
        return "\n".join(lines)


def _indices_to_bboxes(region: RegionData, indices: list[int]) -> list[tuple[float, float, float, float]]:
    if not indices or not region.chars:
        return []
    bbox_by_index = {int(ch.index): ch.bbox for ch in region.chars}
    out: list[tuple[float, float, float, float]] = []
    for i in indices:
        bb = bbox_by_index.get(int(i))
        if bb is not None:
            out.append(bb)
    return out


def _dedupe_keep_order(values: list[int]) -> list[int]:
    seen: set[int] = set()
    out: list[int] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _is_punctuation(ch: str) -> bool:
    if not ch:
        return False
    cat = unicodedata.category(ch)
    return cat.startswith("P")


def _merge_key_value_pairs(pairs: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Merge newline after ':'/'：' when next line is a value continuation."""
    if not pairs:
        return pairs

    out: list[tuple[str, int]] = []
    i = 0
    n = len(pairs)
    while i < n:
        ch, idx = pairs[i]
        out.append((ch, idx))
        if ch not in (":", "："):
            i += 1
            continue

        j = i + 1
        saw_newline = False
        first_newline_idx = idx
        while j < n and pairs[j][0] in ("\r", "\n", " ", "\t"):
            if pairs[j][0] in ("\r", "\n") and not saw_newline:
                saw_newline = True
                first_newline_idx = pairs[j][1]
            j += 1

        if saw_newline and j < n:
            nxt = pairs[j][0]
            if nxt not in ("[", "【", "\r", "\n"):
                out.append((" ", first_newline_idx))
                i = j
                continue

        i += 1

    return out


def _normalize_region_chars_with_map(
    chars: list[CharData],
    *,
    pure_content_mode: bool,
    ignore_punctuation: bool,
    normalize_numbers: bool,
    merge_key_value_lines: bool,
) -> tuple[str, list[int]]:
    """Normalize extracted chars while preserving a per-char back-reference map."""

    pairs: list[tuple[str, int]] = [(ch.char, int(ch.index)) for ch in chars]

    if merge_key_value_lines:
        pairs = _merge_key_value_pairs(pairs)

    stage: list[tuple[str, int]] = []
    for ch, idx in pairs:
        if ch in _ZERO_WIDTH:
            continue
        if ch in ("\r", "\n"):
            stage.append((" ", idx))
            continue

        nfc = unicodedata.normalize("NFC", ch)
        if not nfc:
            continue

        # Keep compatibility with prior full-width normalization behavior.
        nk = unicodedata.normalize("NFKC", nfc)
        if not nk:
            continue

        for cc in nk:
            stage.append((cc, idx))

    if normalize_numbers:
        num_stage: list[tuple[str, int]] = []
        n = len(stage)
        for i, (ch, idx) in enumerate(stage):
            if ch == ",":
                prev_digit = i > 0 and stage[i - 1][0].isdigit()
                next3 = i + 3 < n and all(stage[i + k][0].isdigit() for k in (1, 2, 3))
                next4_not_digit = (i + 4 >= n) or (not stage[i + 4][0].isdigit())
                if prev_digit and next3 and next4_not_digit:
                    continue
            num_stage.append((ch, idx))
        stage = num_stage

    if ignore_punctuation:
        stage = [(ch, idx) for ch, idx in stage if not _is_punctuation(ch)]

    # Trim + collapse whitespace to one space.
    collapsed: list[tuple[str, int]] = []
    pending_space: tuple[str, int] | None = None

    for ch, idx in stage:
        if ch.isspace():
            if collapsed:
                pending_space = (" ", idx)
            continue

        if pending_space is not None:
            collapsed.append(pending_space)
            pending_space = None
        collapsed.append((ch, idx))

    if pure_content_mode:
        collapsed = [(ch, idx) for ch, idx in collapsed if not ch.isspace()]

    text = "".join(ch for ch, _ in collapsed)
    index_map = [idx for _, idx in collapsed]
    return text, index_map


def _diff_normalized_texts(
    left_text: str,
    left_map: list[int],
    right_text: str,
    right_map: list[int],
    left_region: RegionData,
    right_region: RegionData,
) -> list[DiffOp]:
    if left_text == right_text:
        return []

    token_ranges = _diff_by_token_anchors(left_text, right_text)
    if token_ranges is not None:
        token_ops: list[DiffOp] = []
        for i1, i2, j1, j2, tag in token_ranges:
            local_ops = _diff_local_char_ranges(left_text, right_text, i1, i2, j1, j2, tag)
            for li1, li2, rj1, rj2, local_tag in local_ops:
                left_seg = left_text[li1:li2]
                right_seg = right_text[rj1:rj2]
                if not left_seg and not right_seg:
                    continue

                left_indices = _dedupe_keep_order([left_map[k] for k in range(li1, li2) if 0 <= k < len(left_map)])
                right_indices = _dedupe_keep_order([right_map[k] for k in range(rj1, rj2) if 0 <= k < len(right_map)])

                if local_tag == "delete":
                    op_type = DiffOpType.DEL
                elif local_tag == "insert":
                    op_type = DiffOpType.ADD
                else:
                    op_type = DiffOpType.REPLACE

                token_ops.append(
                    DiffOp(
                        type=op_type,
                        left_indices=left_indices,
                        right_indices=right_indices,
                        left_bboxes=_indices_to_bboxes(left_region, left_indices),
                        right_bboxes=_indices_to_bboxes(right_region, right_indices),
                        meta={"left_text": left_seg, "right_text": right_seg},
                    )
                )
        return token_ops

    # Fallback: legacy char-level LCS alignment.
    matches = _lcs_match_pairs(left_text, right_text, use_similarity=False)
    matches.append((len(left_text), len(right_text)))

    ops: list[DiffOp] = []
    ai = bi = 0

    for mi, mj in matches:
        left_seg = left_text[ai:mi]
        right_seg = right_text[bi:mj]

        if left_seg or right_seg:
            left_indices = _dedupe_keep_order([left_map[k] for k in range(ai, mi) if 0 <= k < len(left_map)])
            right_indices = _dedupe_keep_order([right_map[k] for k in range(bi, mj) if 0 <= k < len(right_map)])

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
                    left_bboxes=_indices_to_bboxes(left_region, left_indices),
                    right_bboxes=_indices_to_bboxes(right_region, right_indices),
                    meta={"left_text": left_seg, "right_text": right_seg},
                )
            )

        if mi < len(left_text) and mj < len(right_text):
            ai = mi + 1
            bi = mj + 1
        else:
            ai = mi
            bi = mj

    return ops


def _tokenize_with_spans(text: str) -> list[tuple[str, int, int]]:
    tokens: list[tuple[str, int, int]] = []
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue

        start = i
        if "\u4e00" <= ch <= "\u9fff":
            i += 1
            while i < n and ("\u4e00" <= text[i] <= "\u9fff"):
                i += 1
        elif ch.isascii() and ch.isalnum():
            i += 1
            while i < n and text[i].isascii() and (text[i].isalnum() or text[i] in {"_", "-", "/", "."}):
                i += 1
        else:
            i += 1

        tokens.append((text[start:i], start, i))
    return tokens


def _diff_by_token_anchors(left_text: str, right_text: str) -> list[tuple[int, int, int, int, str]] | None:
    left_tokens = _tokenize_with_spans(left_text)
    right_tokens = _tokenize_with_spans(right_text)
    if not left_tokens or not right_tokens:
        return None

    a = [t[0] for t in left_tokens]
    b = [t[0] for t in right_tokens]
    sm = SequenceMatcher(a=a, b=b, autojunk=False)
    opcodes = sm.get_opcodes()
    if not opcodes:
        return None

    def _char_range(tokens: list[tuple[str, int, int]], s: int, e: int) -> tuple[int, int]:
        if s >= e:
            if s >= len(tokens):
                end = tokens[-1][2] if tokens else 0
                return (end, end)
            p = tokens[s][1]
            return (p, p)
        return (tokens[s][1], tokens[e - 1][2])

    out: list[tuple[int, int, int, int, str]] = []
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            continue
        li1, li2 = _char_range(left_tokens, i1, i2)
        rj1, rj2 = _char_range(right_tokens, j1, j2)
        out.append((li1, li2, rj1, rj2, tag))
    return out


def _diff_local_char_ranges(
    left_text: str,
    right_text: str,
    i1: int,
    i2: int,
    j1: int,
    j2: int,
    tag: str,
) -> list[tuple[int, int, int, int, str]]:
    # For pure insert/delete, segment-level output is already minimal.
    if tag in {"insert", "delete"}:
        return [(i1, i2, j1, j2, tag)]

    # For replace, do local char-level refinement to avoid over-broad replacements.
    la = left_text[i1:i2]
    rb = right_text[j1:j2]
    if la == rb:
        return []

    matches = _lcs_match_pairs(la, rb, use_similarity=False)
    matches.append((len(la), len(rb)))
    ai = bi = 0
    out: list[tuple[int, int, int, int, str]] = []
    for mi, mj in matches:
        lseg = la[ai:mi]
        rseg = rb[bi:mj]
        if lseg or rseg:
            li1 = i1 + ai
            li2 = i1 + mi
            rj1 = j1 + bi
            rj2 = j1 + mj
            if lseg and not rseg:
                out.append((li1, li2, rj1, rj2, "delete"))
            elif rseg and not lseg:
                out.append((li1, li2, rj1, rj2, "insert"))
            else:
                out.append((li1, li2, rj1, rj2, "replace"))

        if mi < len(la) and mj < len(rb):
            ai = mi + 1
            bi = mj + 1
        else:
            ai = mi
            bi = mj
    return out


def _is_trivial_fragment(text: str) -> bool:
    if not text:
        return True
    cleaned = normalize_text(
        text,
        aggressive=True,
        remove_all_whitespace=True,
        remove_punctuation=True,
        normalize_numbers=True,
    )
    return cleaned == ""


def _is_trivial_text_op(op: DiffOp) -> bool:
    left_text = str(op.meta.get("left_text", ""))
    right_text = str(op.meta.get("right_text", ""))
    return _is_trivial_fragment(left_text) and _is_trivial_fragment(right_text)


def _coalesce_text_ops(ops: list[DiffOp], *, max_index_gap: int = 2) -> list[DiffOp]:
    if not ops:
        return []

    def _gap(prev_indices: list[int], curr_indices: list[int]) -> int:
        if not prev_indices or not curr_indices:
            return 0
        return int(curr_indices[0]) - int(prev_indices[-1]) - 1

    def _merge_type(a: DiffOpType, b: DiffOpType) -> DiffOpType:
        return a if a == b else DiffOpType.REPLACE

    merged: list[DiffOp] = []
    current = ops[0]
    for nxt in ops[1:]:
        left_gap = _gap(current.left_indices, nxt.left_indices)
        right_gap = _gap(current.right_indices, nxt.right_indices)
        can_merge = left_gap <= max_index_gap and right_gap <= max_index_gap

        if can_merge:
            current = DiffOp(
                type=_merge_type(current.type, nxt.type),
                left_indices=[*current.left_indices, *nxt.left_indices],
                right_indices=[*current.right_indices, *nxt.right_indices],
                left_bboxes=[*current.left_bboxes, *nxt.left_bboxes],
                right_bboxes=[*current.right_bboxes, *nxt.right_bboxes],
                meta={
                    "left_text": f"{current.meta.get('left_text', '')}{nxt.meta.get('left_text', '')}",
                    "right_text": f"{current.meta.get('right_text', '')}{nxt.meta.get('right_text', '')}",
                },
            )
            continue

        merged.append(current)
        current = nxt

    merged.append(current)
    return merged


def diff_regions(
    region_left: RegionData,
    region_right: RegionData,
    *,
    pure_content_mode: bool = False,
    ignore_punctuation: bool = False,
    normalize_numbers: bool = False,
    merge_key_value_lines: bool = False,
    suppress_trivial_diffs: bool = True,
    coalesce_nearby_text_ops: bool = True,
) -> tuple[list[DiffOp], NormalizationLog]:
    norm_log = NormalizationLog()
    norm_log.add("编码统一", True)
    norm_log.add("全角转半角", True)
    norm_log.add("空白压缩", True)
    norm_log.add("键值断行合并", merge_key_value_lines)
    norm_log.add("忽略标点", ignore_punctuation)
    norm_log.add("忽略空格", pure_content_mode)
    norm_log.add("数字标准化", normalize_numbers)

    left_norm, left_map = _normalize_region_chars_with_map(
        region_left.chars,
        pure_content_mode=pure_content_mode,
        ignore_punctuation=ignore_punctuation,
        normalize_numbers=normalize_numbers,
        merge_key_value_lines=merge_key_value_lines,
    )
    right_norm, right_map = _normalize_region_chars_with_map(
        region_right.chars,
        pure_content_mode=pure_content_mode,
        ignore_punctuation=ignore_punctuation,
        normalize_numbers=normalize_numbers,
        merge_key_value_lines=merge_key_value_lines,
    )

    text_ops = _diff_normalized_texts(
        left_norm,
        left_map,
        right_norm,
        right_map,
        region_left,
        region_right,
    )

    if suppress_trivial_diffs:
        text_ops = [op for op in text_ops if not _is_trivial_text_op(op)]
    if coalesce_nearby_text_ops:
        text_ops = _coalesce_text_ops(text_ops)

    format_ops = []
    if not pure_content_mode:
        format_ops = format_diff_regions(region_left, region_right)

    ops = [*text_ops, *format_ops]

    def _sort_key(op: DiffOp) -> int:
        if op.left_indices:
            return int(op.left_indices[0])
        if op.right_indices:
            return int(op.right_indices[0])
        return 0

    return sorted(ops, key=_sort_key), norm_log


def _op_to_dict(op: DiffOp) -> dict:
    d = asdict(op)
    d["type"] = op.type.value
    return d


def main() -> int:
    from .models import RegionData, StyleFlags

    left = RegionData(
        page_number=0,
        bboxes=[],
        chars=[
            CharData("a", 0, (0, 0, 1, 1), "F", "F", 10.0, (0, 0, 0), StyleFlags(False, False)),
            CharData("b", 1, (1, 0, 2, 1), "F", "F", 10.0, (0, 0, 0), StyleFlags(False, False)),
        ],
    )
    right = RegionData(
        page_number=0,
        bboxes=[],
        chars=[
            CharData("a", 10, (0, 0, 1, 1), "F", "F", 11.0, (0, 0, 0), StyleFlags(False, False)),
            CharData("c", 11, (1, 0, 2, 1), "F", "F", 10.0, (0, 0, 0), StyleFlags(False, False)),
        ],
    )

    ops, _ = diff_regions(left, right)
    print(json.dumps([_op_to_dict(o) for o in ops], ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
