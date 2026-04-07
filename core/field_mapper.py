"""Field-level mapping and comparison utilities.

This module provides a robust key-value extraction path for PDF text where
line breaks and punctuation can vary across layouts.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass
class KeyValueMatch:
    key: str
    value: str
    key_pos: tuple[int, int]
    value_pos: tuple[int, int]
    canonical_key: str


@dataclass
class FieldDiff:
    field_name: str
    left_value: str | None
    right_value: str | None
    diff_type: str
    left_pos: tuple[int, int] | None
    right_pos: tuple[int, int] | None


_FIELD_SYNONYMS: dict[str, str] = {
    "地址": "生产地址",
    "生产地址": "生产地址",
    "注册地址": "注册地址",
    "企业名称": "企业名称",
    "公司名称": "企业名称",
    "电话": "电话",
    "联系电话": "电话",
    "传真": "传真",
    "传真号码": "传真",
    "邮编": "邮编",
    "网址": "网址",
    "网站": "网址",
    "邮箱": "邮箱",
    "电子邮箱": "邮箱",
    "产品名称": "产品名称",
    "药品名称": "产品名称",
    "规格": "规格",
    "剂型": "剂型",
    "批准文号": "批准文号",
    "成份": "成份",
    "适应症": "适应症",
    "用法用量": "用法用量",
    "不良反应": "不良反应",
    "禁忌": "禁忌",
    "贮藏": "贮藏",
    "有效期": "有效期",
    "名称": "企业名称",
    "企业": "生产企业",
    "生产企业": "生产企业",
    "电话号码": "电话",
    "邮政编码": "邮编",
}

_KNOWN_FIELD_VARIANTS: list[str] = sorted(_FIELD_SYNONYMS.keys(), key=len, reverse=True)
_CONTAINER_FIELDS: set[str] = {"生产企业"}
_STRUCTURED_CONTACT_FIELDS: set[str] = {"企业名称", "生产地址", "邮编", "电话", "传真", "网址", "邮箱"}


def normalize_field_name(text: str) -> str:
    key = text.strip()
    key = key.strip("[]【】()（）")
    key = re.sub(r"\s+", "", key)
    key = key.rstrip(":：")
    return _FIELD_SYNONYMS.get(key, key)


def _iter_line_key_values(text: str) -> list[KeyValueMatch]:
    """Extract key-value pairs from `key: value` and wrapped key/value lines."""
    lines = text.splitlines()
    out: list[KeyValueMatch] = []
    cursor = 0

    for i, line in enumerate(lines):
        line_start = cursor
        line_end = cursor + len(line)

        # Section headers like 【用法用量】...
        sec = re.match(r"^\s*[【\[]\s*([^】\]]+?)\s*[】\]]\s*(.*?)\s*$", line)
        if sec:
            key_raw = sec.group(1).strip()
            value_raw = sec.group(2).strip()
            canonical = normalize_field_name(key_raw)
            key_s = line.find(key_raw)
            key_pos = (line_start + key_s, line_start + key_s + len(key_raw))
            if value_raw:
                value_s = line.find(value_raw, key_s + len(key_raw))
                value_pos = (line_start + value_s, line_start + value_s + len(value_raw))
            else:
                value_pos = (line_end, line_end)
            out.append(KeyValueMatch(key_raw, value_raw, key_pos, value_pos, canonical))
            cursor += len(line) + 1
            continue

        # Inline key-value.
        m = re.match(r"^\s*([^:：\n]{1,40}?)\s*[:：]\s*(.*?)\s*$", line)
        if m:
            key_raw = m.group(1).strip()
            value_raw = m.group(2).strip()

            # Wrapped value on next non-empty line.
            if not value_raw:
                for j in range(i + 1, len(lines)):
                    nxt = lines[j].strip()
                    if nxt:
                        value_raw = nxt
                        break

            canonical = normalize_field_name(key_raw)
            key_s = line.find(key_raw)
            key_pos = (line_start + key_s, line_start + key_s + len(key_raw))

            if value_raw:
                value_s = line.find(value_raw)
                if value_s >= 0:
                    value_pos = (line_start + value_s, line_start + value_s + len(value_raw))
                else:
                    value_pos = (line_end, line_end + len(value_raw))
            else:
                value_pos = (line_end, line_end)

            out.append(KeyValueMatch(key_raw, value_raw, key_pos, value_pos, canonical))

        cursor += len(line) + 1

    return out


def _iter_stream_key_values(text: str) -> list[KeyValueMatch]:
    """Extract key-value pairs from continuous text without line breaks."""
    out: list[KeyValueMatch] = []

    # Key: value ... (next key:) or end
    pattern = re.compile(
        r"([\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff()（）【】\[\]\-]{1,24})\s*[:：]\s*"
        r"(.{1,120}?)"
        r"(?=(?:[\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff()（）【】\[\]\-]{1,24}\s*[:：])|$)"
    )

    for m in pattern.finditer(text):
        key_raw = m.group(1).strip()
        value_raw = m.group(2).strip()
        if not key_raw or not value_raw:
            continue
        # Avoid clearly broken one-character keys.
        if len(key_raw) < 2:
            continue
        if "http" in key_raw.lower():
            continue
        if not _looks_like_field_key(key_raw):
            continue
        canonical = normalize_field_name(key_raw)
        out.append(
            KeyValueMatch(
                key=key_raw,
                value=value_raw,
                key_pos=(m.start(1), m.end(1)),
                value_pos=(m.start(2), m.end(2)),
                canonical_key=canonical,
            )
        )

    return out


def _looks_like_field_key(key_raw: str) -> bool:
    k = normalize_field_name(key_raw)
    if k in _FIELD_SYNONYMS.values():
        return True
    # Avoid merged long chunks being treated as keys.
    if len(key_raw) > 12:
        return False
    if any(ch.isdigit() for ch in key_raw):
        return False
    terms = ["名称", "地址", "电话", "传真", "邮编", "编码", "网址", "邮箱", "文号", "规格", "剂型"]
    hit_terms = sum(1 for t in terms if t in key_raw)
    if hit_terms >= 2:
        return False
    return hit_terms == 1


def _iter_known_key_segments(text: str) -> list[KeyValueMatch]:
    """Extract key-values from text without colon by scanning known field variants."""
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return []

    hits: list[tuple[int, str]] = []
    for key in _KNOWN_FIELD_VARIANTS:
        start = 0
        while True:
            pos = compact.find(key, start)
            if pos < 0:
                break
            hits.append((pos, key))
            start = pos + len(key)

    if not hits:
        return []

    # Keep first key occurrence at each position (longest key due sorted variants).
    hits.sort(key=lambda x: (x[0], -len(x[1])))
    merged_hits: list[tuple[int, str]] = []
    used_pos: set[int] = set()
    for pos, key in hits:
        if pos in used_pos:
            continue
        used_pos.add(pos)
        merged_hits.append((pos, key))

    out: list[KeyValueMatch] = []
    for i, (pos, key) in enumerate(merged_hits):
        key_end = pos + len(key)
        next_pos = merged_hits[i + 1][0] if i + 1 < len(merged_hits) else len(compact)
        value = compact[key_end:next_pos].strip("：:，,;；。 ")
        if not value:
            continue
        if len(value) <= 1 and not value.isdigit():
            continue
        canonical = normalize_field_name(key)
        out.append(
            KeyValueMatch(
                key=key,
                value=value,
                key_pos=(pos, key_end),
                value_pos=(key_end, next_pos),
                canonical_key=canonical,
            )
        )
    return out


def extract_key_values(text: str) -> list[KeyValueMatch]:
    if not text:
        return []

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    kvs = _iter_line_key_values(normalized)
    kvs.extend(_iter_stream_key_values(normalized))
    kvs.extend(_iter_known_key_segments(normalized))

    # Deduplicate by canonical key: keep the first non-empty value, otherwise first seen.
    chosen: dict[str, KeyValueMatch] = {}
    for kv in kvs:
        prev = chosen.get(kv.canonical_key)
        if prev is None:
            chosen[kv.canonical_key] = kv
            continue
        if (not prev.value) and kv.value:
            chosen[kv.canonical_key] = kv
            continue
        # Prefer cleaner/shorter values to avoid runaway captures.
        if kv.value and len(kv.value) < len(prev.value):
            chosen[kv.canonical_key] = kv

    return list(chosen.values())


def should_enable_field_mapping(
    left_text: str,
    right_text: str,
    left_kvs: list[KeyValueMatch] | None = None,
    right_kvs: list[KeyValueMatch] | None = None,
) -> tuple[bool, str]:
    """Decide whether field-level comparison is meaningful for the current regions.

    This prevents noisy field diffs on narrative paragraphs where key-value structure
    is absent.
    """
    if left_kvs is None:
        left_kvs = extract_key_values(left_text)
    if right_kvs is None:
        right_kvs = extract_key_values(right_text)

    left_keys = {kv.canonical_key for kv in left_kvs}
    right_keys = {kv.canonical_key for kv in right_kvs}
    shared_keys = left_keys.intersection(right_keys)

    # Structure evidence from explicit delimiters and extracted fields.
    left_colons = (left_text or "").count(":") + (left_text or "").count("：")
    right_colons = (right_text or "").count(":") + (right_text or "").count("：")

    left_structured = len(left_kvs) >= 2 and (left_colons >= 1 or len(left_keys) >= 3)
    right_structured = len(right_kvs) >= 2 and (right_colons >= 1 or len(right_keys) >= 3)

    if left_structured and right_structured and len(shared_keys) >= 2:
        return True, ""

    reason = f"结构化证据不足: left_kvs={len(left_kvs)}, right_kvs={len(right_kvs)}, shared_keys={len(shared_keys)}"
    return False, reason


def compare_by_fields(
    left_text: str,
    right_text: str,
    left_kvs: list[KeyValueMatch] | None = None,
    right_kvs: list[KeyValueMatch] | None = None,
) -> list[FieldDiff]:
    if left_kvs is None:
        left_kvs = extract_key_values(left_text)
    if right_kvs is None:
        right_kvs = extract_key_values(right_text)

    left_by_key = {kv.canonical_key: kv for kv in left_kvs}
    right_by_key = {kv.canonical_key: kv for kv in right_kvs}

    all_keys = sorted(set(left_by_key) | set(right_by_key))
    # Drop noisy container fields when structured subfields are already available on both sides.
    shared_structured = set(left_by_key).intersection(right_by_key).intersection(_STRUCTURED_CONTACT_FIELDS)
    if len(shared_structured) >= 3:
        all_keys = [k for k in all_keys if k not in _CONTAINER_FIELDS]

    diffs: list[FieldDiff] = []

    for key in all_keys:
        l = left_by_key.get(key)
        r = right_by_key.get(key)

        if l and r:
            diff_type = "match" if _field_values_equal(key, l.value, r.value) else "replace"
            diffs.append(
                FieldDiff(
                    field_name=key,
                    left_value=l.value,
                    right_value=r.value,
                    diff_type=diff_type,
                    left_pos=l.value_pos,
                    right_pos=r.value_pos,
                )
            )
        elif l:
            diffs.append(
                FieldDiff(
                    field_name=key,
                    left_value=l.value,
                    right_value=None,
                    diff_type="del",
                    left_pos=l.value_pos,
                    right_pos=None,
                )
            )
        elif r:
            diffs.append(
                FieldDiff(
                    field_name=key,
                    left_value=None,
                    right_value=r.value,
                    diff_type="add",
                    left_pos=None,
                    right_pos=r.value_pos,
                )
            )

    return diffs


def _normalize_url_text(text: str) -> str:
    v = (text or "").strip().lower()
    v = re.sub(r"^https?://", "", v)
    v = re.sub(r"^www\.", "www", v)
    return re.sub(r"[^a-z0-9]", "", v)


def _field_values_equal(field_name: str, left: str, right: str) -> bool:
    if left == right:
        return True

    if field_name == "网址":
        l_url = _normalize_url_text(left or "")
        r_url = _normalize_url_text(right or "")
        if l_url and l_url == r_url:
            return True
        if l_url and r_url:
            # Handle punctuation-loss / text-layer degradation in compressed PDFs.
            return SequenceMatcher(None, l_url, r_url).ratio() >= 0.90

    left_norm = _normalize_generic_text(left)
    right_norm = _normalize_generic_text(right)
    if left_norm == right_norm:
        return True

    if field_name in {"企业名称", "产品名称", "生产地址", "注册地址"}:
        if left_norm and right_norm:
            if left_norm in right_norm or right_norm in left_norm:
                return True
            if SequenceMatcher(None, left_norm, right_norm).ratio() >= 0.95:
                return True

    if field_name in {"电话", "传真", "邮编"}:
        l_digits = re.sub(r"\D", "", left_norm)
        r_digits = re.sub(r"\D", "", right_norm)
        if bool(l_digits) and l_digits == r_digits:
            return True
        # Handle merged value where one side accidentally includes adjacent field digits.
        return bool(l_digits) and l_digits in r_digits

    # For high-noise text layer: if normalized similarity is very high, consider equal.
    if left_norm and right_norm:
        return SequenceMatcher(None, left_norm, right_norm).ratio() >= 0.98
    return False


def _normalize_generic_text(text: str) -> str:
    v = unicodedata.normalize("NFKC", text or "").lower()
    v = re.sub(r"\s+", "", v)
    v = re.sub(r"[，,。．\.；;：:\-—_（）()【】\[\]“”\"'‘’]", "", v)
    return v


def format_field_diff_description(diff: FieldDiff) -> str:
    if diff.diff_type == "match":
        return f"[OK] {diff.field_name}: 一致"
    if diff.diff_type == "add":
        return f"[+] 新增字段[{diff.field_name}]: {diff.right_value}"
    if diff.diff_type == "del":
        return f"[-] 删除字段[{diff.field_name}]: {diff.left_value}"
    if diff.diff_type == "replace":
        return f"[*] 字段[{diff.field_name}]值不同: 左={diff.left_value} | 右={diff.right_value}"
    return f"[?] 未知差异: {diff.field_name}"
