"""Region management for manual user selections.

This module handles saving, loading and managing user's manual region selections.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .app_paths import runtime_state_path
from .models import BBox


class RegionSelection:
    """Represents a single region selection by user."""

    def __init__(
        self,
        name: str,
        left_page: int,
        left_bbox: BBox,
        right_page: int,
        right_bbox: BBox,
        timestamp: datetime | None = None,
    ):
        self.name = name
        self.left_page = left_page
        self.left_bbox = left_bbox
        self.right_page = right_page
        self.right_bbox = right_bbox
        self.timestamp = timestamp or datetime.now()

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "left_page": self.left_page,
            "left_bbox": list(self.left_bbox),
            "right_page": self.right_page,
            "right_bbox": list(self.right_bbox),
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegionSelection:
        """Create from dictionary."""
        return cls(
            name=data["name"],
            left_page=data["left_page"],
            left_bbox=tuple(data["left_bbox"]),
            right_page=data["right_page"],
            right_bbox=tuple(data["right_bbox"]),
            timestamp=datetime.fromisoformat(data["timestamp"]),
        )


class RegionManager:
    """Manages user's manual region selections."""

    def __init__(self, storage_path: Path):
        self.storage_path = storage_path
        self.selections: list[RegionSelection] = []
        self.logger = logging.getLogger(__name__)
        self._io_lock = threading.RLock()

        # Ensure storage directory exists
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        # Load existing selections
        self._load_selections()

    def _load_selections(self) -> None:
        """Load region selections from storage."""
        if not self.storage_path.exists():
            return

        try:
            with self._io_lock, open(self.storage_path, encoding="utf-8") as f:
                data = json.load(f)

            self.selections = [RegionSelection.from_dict(item) for item in data.get("selections", [])]

            self.logger.info(f"Loaded {len(self.selections)} region selections")

        except Exception as e:
            self.logger.error(f"Failed to load region selections: {e}")
            # Keep broken file for forensic/debug instead of repeatedly crashing on startup.
            try:
                broken = self.storage_path.with_suffix(
                    f"{self.storage_path.suffix}.corrupt.{datetime.now().strftime('%Y%m%d%H%M%S')}"
                )
                if self.storage_path.exists():
                    os.replace(self.storage_path, broken)
                    self.logger.error(f"Corrupted selections moved to: {broken}")
            except Exception:
                pass
            self.selections = []

    def save_selections(self) -> None:
        """Save current region selections to storage."""
        try:
            data = {"version": "1.0", "selections": [sel.to_dict() for sel in self.selections]}

            tmp_path = self.storage_path.with_suffix(f"{self.storage_path.suffix}.tmp")
            with self._io_lock:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, self.storage_path)

            self.logger.info(f"Saved {len(self.selections)} region selections")

        except Exception as e:
            self.logger.error(f"Failed to save region selections: {e}")

    def add_selection(
        self, name: str, left_page: int, left_bbox: BBox, right_page: int, right_bbox: BBox
    ) -> RegionSelection:
        """Add a new region selection."""
        # Generate unique name if needed
        if not name:
            name = f"Selection {len(self.selections) + 1}"

        # Check if name already exists
        existing_names = [sel.name for sel in self.selections]
        if name in existing_names:
            # Append timestamp to make it unique
            timestamp = datetime.now().strftime("%H%M%S")
            name = f"{name} ({timestamp})"

        selection = RegionSelection(name, left_page, left_bbox, right_page, right_bbox)
        self.selections.append(selection)
        self.save_selections()

        return selection

    def get_selection(self, name: str) -> RegionSelection | None:
        """Get a selection by name."""
        for sel in self.selections:
            if sel.name == name:
                return sel
        return None

    def delete_selection(self, name: str) -> bool:
        """Delete a selection by name."""
        for i, sel in enumerate(self.selections):
            if sel.name == name:
                del self.selections[i]
                self.save_selections()
                return True
        return False

    def list_selections(self) -> list[RegionSelection]:
        """List all selections sorted by timestamp (newest first)."""
        return sorted(self.selections, key=lambda x: x.timestamp, reverse=True)

    def rename_selection(self, old_name: str, new_name: str) -> bool:
        """Rename a selection."""
        for sel in self.selections:
            if sel.name == old_name:
                sel.name = new_name
                self.save_selections()
                return True
        return False

    def export_selections(self, export_path: Path) -> None:
        """Export selections to a portable format."""
        data = {
            "export_version": "1.0",
            "export_date": datetime.now().isoformat(),
            "selections": [sel.to_dict() for sel in self.selections],
        }

        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def import_selections(self, import_path: Path) -> int:
        """Import selections from a portable format."""
        try:
            with open(import_path, encoding="utf-8") as f:
                data = json.load(f)

            imported_count = 0
            for item in data.get("selections", []):
                selection = RegionSelection.from_dict(item)

                # Avoid duplicates
                if not any(sel.name == selection.name for sel in self.selections):
                    self.selections.append(selection)
                    imported_count += 1

            if imported_count > 0:
                self.save_selections()
                self.logger.info(f"Imported {imported_count} region selections")

            return imported_count

        except Exception as e:
            self.logger.error(f"Failed to import selections: {e}")
            return 0


def get_default_manager() -> RegionManager:
    """Get default region manager instance."""
    return RegionManager(runtime_state_path("region_selections.json"))
