from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CompareViewModel:
    summary: str
    warn: bool
    ocr_state: str
    ocr_state_reason: str
    warnings: list[str]
    decision_basis: str = "text"
    gate_reason: str = ""
    fallback_reason: str = ""
    quality_scores: dict[str, int] | None = None
