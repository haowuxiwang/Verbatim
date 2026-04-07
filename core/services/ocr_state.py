from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OcrRunState(str, Enum):
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILURE = "failure"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class OcrRunStatus:
    state: OcrRunState
    reason: str
    detail: str = ""


def compute_ocr_status(
    *,
    use_ocr: bool,
    can_run_ocr: bool,
    has_ocr_config: bool,
    ocr_was_recommended: bool,
    replaced_sides: list[str],
    attempted_but_empty: bool,
) -> OcrRunStatus:
    if not use_ocr:
        return OcrRunStatus(OcrRunState.BLOCKED, "disabled")
    if not can_run_ocr:
        return OcrRunStatus(OcrRunState.BLOCKED, "cannot_run")
    if not has_ocr_config:
        return OcrRunStatus(OcrRunState.BLOCKED, "no_config")
    if not ocr_was_recommended:
        return OcrRunStatus(OcrRunState.BLOCKED, "not_recommended")
    if replaced_sides:
        detail = ",".join(replaced_sides)
        if len(replaced_sides) >= 2:
            return OcrRunStatus(OcrRunState.SUCCESS, "applied", detail)
        return OcrRunStatus(OcrRunState.PARTIAL, "applied_partial", detail)
    if attempted_but_empty:
        return OcrRunStatus(OcrRunState.FAILURE, "attempted_empty")
    return OcrRunStatus(OcrRunState.FAILURE, "unknown")
