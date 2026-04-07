from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .app_paths import runtime_state_path

BBox = tuple[float, float, float, float]


@dataclass
class CompareRecord:
    timestamp: datetime
    status: str
    summary: str
    ops_count: int
    field_diffs_count: int
    ocr_used: bool
    compare_status: str
    reliability: str
    ocr_state: str
    ocr_state_reason: str
    decision_basis: str
    gate_reason: str
    fallback_reason: str
    left_page: int
    right_page: int
    left_bbox: BBox | None
    right_bbox: BBox | None
    warnings_count: int
    diff_ops: list[dict[str, Any]]
    left_region_text: str
    right_region_text: str
    left_ocr_applied: bool
    right_ocr_applied: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "status": self.status,
            "summary": self.summary,
            "ops_count": int(self.ops_count),
            "field_diffs_count": int(self.field_diffs_count),
            "ocr_used": bool(self.ocr_used),
            "compare_status": self.compare_status,
            "reliability": self.reliability,
            "ocr_state": self.ocr_state,
            "ocr_state_reason": self.ocr_state_reason,
            "decision_basis": self.decision_basis,
            "gate_reason": self.gate_reason,
            "fallback_reason": self.fallback_reason,
            "left_page": int(self.left_page),
            "right_page": int(self.right_page),
            "left_bbox": list(self.left_bbox) if self.left_bbox is not None else None,
            "right_bbox": list(self.right_bbox) if self.right_bbox is not None else None,
            "warnings_count": int(self.warnings_count),
            "diff_ops": list(self.diff_ops),
            "left_region_text": self.left_region_text,
            "right_region_text": self.right_region_text,
            "left_ocr_applied": bool(self.left_ocr_applied),
            "right_ocr_applied": bool(self.right_ocr_applied),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompareRecord:
        return cls(
            timestamp=datetime.fromisoformat(str(data["timestamp"])),
            status=str(data.get("status", "unknown")),
            summary=str(data.get("summary", "")),
            ops_count=int(data.get("ops_count", 0)),
            field_diffs_count=int(data.get("field_diffs_count", 0)),
            ocr_used=bool(data.get("ocr_used", False)),
            compare_status=str(data.get("compare_status", "PASS")),
            reliability=str(data.get("reliability", "未知")),
            ocr_state=str(data.get("ocr_state", "unknown")),
            ocr_state_reason=str(data.get("ocr_state_reason", "")),
            decision_basis=str(data.get("decision_basis", "text")),
            gate_reason=str(data.get("gate_reason", "")),
            fallback_reason=str(data.get("fallback_reason", "")),
            left_page=int(data.get("left_page", 0)),
            right_page=int(data.get("right_page", 0)),
            left_bbox=tuple(data["left_bbox"]) if data.get("left_bbox") else None,
            right_bbox=tuple(data["right_bbox"]) if data.get("right_bbox") else None,
            warnings_count=int(data.get("warnings_count", 0)),
            diff_ops=list(data.get("diff_ops", [])),
            left_region_text=str(data.get("left_region_text", "")),
            right_region_text=str(data.get("right_region_text", "")),
            left_ocr_applied=bool(data.get("left_ocr_applied", False)),
            right_ocr_applied=bool(data.get("right_ocr_applied", False)),
        )


class CompareHistoryManager:
    def __init__(self, storage_path: Path, *, max_records: int = 200):
        self.storage_path = storage_path
        self.max_records = max(20, int(max_records))
        self.records: list[CompareRecord] = []
        self.logger = logging.getLogger(__name__)
        self._io_lock = threading.RLock()
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._load_records()

    def _load_records(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            with self._io_lock, open(self.storage_path, encoding="utf-8") as f:
                data = json.load(f)
            self.records = [CompareRecord.from_dict(item) for item in data.get("records", [])]
        except Exception as e:
            self.logger.error(f"Failed to load compare history: {e}")
            try:
                broken = self.storage_path.with_suffix(
                    f"{self.storage_path.suffix}.corrupt.{datetime.now().strftime('%Y%m%d%H%M%S')}"
                )
                if self.storage_path.exists():
                    os.replace(self.storage_path, broken)
            except Exception:
                pass
            self.records = []

    def _save_records(self) -> None:
        data = {
            "version": "1.0",
            "records": [r.to_dict() for r in self.records],
        }
        tmp_path = self.storage_path.with_suffix(f"{self.storage_path.suffix}.tmp")
        with self._io_lock:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.storage_path)

    def add_record(
        self,
        *,
        status: str,
        summary: str,
        ops_count: int,
        field_diffs_count: int,
        ocr_used: bool,
        compare_status: str,
        reliability: str,
        ocr_state: str = "unknown",
        ocr_state_reason: str = "",
        decision_basis: str = "text",
        gate_reason: str = "",
        fallback_reason: str = "",
        left_page: int,
        right_page: int,
        left_bbox: BBox | None,
        right_bbox: BBox | None,
        warnings_count: int,
        diff_ops: list[dict[str, Any]] | None = None,
        left_region_text: str = "",
        right_region_text: str = "",
        left_ocr_applied: bool = False,
        right_ocr_applied: bool = False,
    ) -> CompareRecord:
        rec = CompareRecord(
            timestamp=datetime.now(),
            status=status,
            summary=summary,
            ops_count=ops_count,
            field_diffs_count=field_diffs_count,
            ocr_used=ocr_used,
            compare_status=compare_status,
            reliability=reliability,
            ocr_state=str(ocr_state),
            ocr_state_reason=str(ocr_state_reason),
            decision_basis=str(decision_basis or "text"),
            gate_reason=str(gate_reason or ""),
            fallback_reason=str(fallback_reason or ""),
            left_page=left_page,
            right_page=right_page,
            left_bbox=left_bbox,
            right_bbox=right_bbox,
            warnings_count=warnings_count,
            diff_ops=list(diff_ops or []),
            left_region_text=str(left_region_text or ""),
            right_region_text=str(right_region_text or ""),
            left_ocr_applied=bool(left_ocr_applied),
            right_ocr_applied=bool(right_ocr_applied),
        )
        self.records.append(rec)
        if len(self.records) > self.max_records:
            self.records = self.records[-self.max_records :]
        self._save_records()
        return rec

    def list_records(self) -> list[CompareRecord]:
        return sorted(self.records, key=lambda r: r.timestamp, reverse=True)


def get_default_manager(*, max_records: int = 200) -> CompareHistoryManager:
    return CompareHistoryManager(runtime_state_path("compare_history.json"), max_records=max_records)
