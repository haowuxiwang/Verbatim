from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FieldMappingResult:
    enabled: bool
    field_diffs: list[Any]
    left_kvs: list[Any]
    right_kvs: list[Any]
    note: str
    disable_reason: str


def run_field_mapping(
    *,
    left_text: str,
    right_text: str,
    extract_key_values: Callable[[str], list[Any]],
    should_enable_field_mapping: Callable[[str, str, list[Any], list[Any]], tuple[bool, str]],
    compare_by_fields: Callable[[str, str, list[Any], list[Any]], list[Any]],
) -> FieldMappingResult:
    left_kvs = extract_key_values(left_text)
    right_kvs = extract_key_values(right_text)
    enabled, disable_reason = should_enable_field_mapping(left_text, right_text, left_kvs, right_kvs)
    if enabled:
        field_diffs = compare_by_fields(left_text, right_text, left_kvs, right_kvs)
        return FieldMappingResult(
            enabled=True,
            field_diffs=field_diffs,
            left_kvs=left_kvs,
            right_kvs=right_kvs,
            note="",
            disable_reason="",
        )
    return FieldMappingResult(
        enabled=False,
        field_diffs=[],
        left_kvs=left_kvs,
        right_kvs=right_kvs,
        note=f"字段比对已跳过（{disable_reason}）",
        disable_reason=disable_reason,
    )
