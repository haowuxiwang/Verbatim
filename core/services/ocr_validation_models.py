from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from core.services.ocr_models import OcrResult


class OcrValidationSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class OcrValidationIssue:
    code: str
    severity: OcrValidationSeverity
    detail: str = ""


@dataclass(frozen=True)
class OcrValidationStatus:
    issues: tuple[OcrValidationIssue, ...]

    @property
    def ok(self) -> bool:
        return not self.issues

    @property
    def has_error(self) -> bool:
        return any(issue.severity == OcrValidationSeverity.ERROR for issue in self.issues)

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)

    @property
    def worst_severity(self) -> OcrValidationSeverity | None:
        if not self.issues:
            return None
        if any(issue.severity == OcrValidationSeverity.ERROR for issue in self.issues):
            return OcrValidationSeverity.ERROR
        if any(issue.severity == OcrValidationSeverity.WARNING for issue in self.issues):
            return OcrValidationSeverity.WARNING
        return OcrValidationSeverity.INFO

    @property
    def penalty(self) -> float:
        severity_weights = {
            OcrValidationSeverity.INFO: 2.0,
            OcrValidationSeverity.WARNING: 8.0,
            OcrValidationSeverity.ERROR: 100.0,
        }
        return float(sum(severity_weights.get(issue.severity, 0.0) for issue in self.issues))


@dataclass(frozen=True)
class OcrValidatedResult:
    result: OcrResult
    status: OcrValidationStatus
