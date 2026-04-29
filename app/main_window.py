"""Verbatim (MVP) - minimal PySide6 UI.

Phase 14 Step 1: file-pick mode (no hardcoded PDFs).

- App starts with empty viewers.
- User clicks buttons to select left/right PDFs.
- Left drag-select (single page) triggers:
  bbox -> extract_region() -> diff_regions() -> console JSON
  and overlays + diff list in UI.
"""

from __future__ import annotations

import ctypes
import json
import os
import pickle
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, cast

import fitz  # PyMuPDF
from PySide6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, Qt, Signal, Slot
from PySide6.QtGui import QColor, QImage, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedLayout,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.diff_presenter import (
    build_diff_details_html,
    build_field_diff_details_html,
    populate_diff_lists,
)
from app.page_quality import evaluate_page_text_layer
from app.view_models import CompareViewModel
from core.compare_history import get_default_manager as get_default_compare_history_manager
from core.diff_regions import diff_regions
from core.field_mapper import (
    FieldDiff,
    compare_by_fields,
    extract_key_values,
    format_field_diff_description,
    should_enable_field_mapping,
)
from core.models import BBox, CharData, DiffOp, DiffOpType, PageData, RegionData, StyleFlags
from core.ocr_client import (
    DEFAULT_JOB_URL,
    DEFAULT_MODEL,
    DEFAULT_SYNC_URL,
    OcrConfig,
    PaddleOcrClient,
    default_ocr_config_path,
    render_pdf_region_png_with_meta,
)
from core.pdf_parser import parse_page
from core.region_extractor import extract_region
from core.region_manager import get_default_manager as get_default_region_manager
from core.services.background_tasks import PrealignComputationResult
from core.services.compare_orchestrator import (
    build_compare_result_summary,
    collect_quality_warnings,
    decide_ocr,
)
from core.services.field_orchestrator import run_field_mapping
from core.services.observability import log_event
from core.services.ocr_engines import (
    CloudPaddleEngine,
    LocalOcrSelfCheck,
    LocalPaddleEngine,
    LocalPaddleOcrJsonEngine,
    resolve_ocr_json_exe_path,
    resolve_ocr_runtime_dir,
    run_local_ocr_self_check,
)
from core.services.ocr_errors import classify_ocr_error
from core.services.ocr_models import OcrResult, OcrSpan
from core.services.ocr_orchestrator import run_ocr_fallback
from core.services.ocr_state import OcrRunState, compute_ocr_status
from core.services.prealign import DocumentProfile, build_document_profile, retrieve_page_candidates, suggest_region_candidates
from core.services.ocr_validation import (
    summarize_text,
    validate_ocr_input,
    validate_ocr_result,
    validate_ocr_spans,
)
from core.services.raster_diff import compute_visual_diff_payload
from core.services.text_quality import (
    check_text_quality as svc_check_text_quality,
)
from core.services.text_quality import (
    garble_signal_score as svc_garble_signal_score,
)
from core.services.text_quality import (
    is_weak_confusable_pair as svc_is_weak_confusable_pair,
)
from core.services.text_quality import (
    normalized_similarity as svc_normalized_similarity,
)
from core.services.text_quality import (
    should_try_ocr_side as svc_should_try_ocr_side,
)

OcrEngine = CloudPaddleEngine | LocalPaddleEngine | LocalPaddleOcrJsonEngine

RENDER_ZOOM = 2.0
ZOOM_MIN = 0.25
ZOOM_MAX = 4.0
ZOOM_STEP = 0.10

# Region management
REGION_MANAGER = get_default_region_manager()
COMPARE_HISTORY_MANAGER = get_default_compare_history_manager()


class OverlayImageLabel(QLabel):
    """A QLabel that can paint simple rectangle overlays over a pixmap."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._overlays: list[tuple[QRectF, QColor, str]] = []  # (rect, color, diff_type)
        self._selected_overlays: list[tuple[QRectF, QColor]] = []
        self._hover_overlay: tuple[QRectF, QColor] | None = None
        self._badges: list[tuple[QRectF, str, QColor]] = []  # (rect, text, color)
        self._show_format_diffs = False  # Default: HIDE format diffs (cleaner UI)
        self._show_badges = True  # Toggle for badge visibility
        self._sync_selection_overlay: QRectF | None = None  # Synchronized selection feedback

    def set_overlays(self, overlays: list[tuple[QRectF, QColor]], diff_type: str = "content") -> None:
        # Store with diff type for filtering - support both old and new format
        converted: list[tuple[QRectF, QColor, str]] = []
        for item in overlays:
            if len(item) == 2:
                rect, color = item
                converted.append((rect, color, diff_type))
            else:
                # Already has diff type
                converted.append(item)  # type: ignore[arg-type]
        self._overlays = converted
        self.update()

    def set_overlays_with_types(self, overlays: list[tuple[QRectF, QColor, str]]) -> None:
        """Set overlays with explicit diff types for filtering."""
        self._overlays = overlays
        self.update()

    def set_selected_overlays(self, overlays: list[tuple[QRectF, QColor]]) -> None:
        self._selected_overlays = overlays
        self.update()

    def set_hover_overlay(self, overlay: tuple[QRectF, QColor] | None) -> None:
        """Set overlay to highlight on hover (for connection line interaction)."""
        self._hover_overlay = overlay
        self.update()

    def set_badges(self, badges: list[tuple[QRectF, str, QColor]]) -> None:
        """Set badges to display (rect, text, color)."""
        self._badges = badges
        self.update()

    def set_show_format_diffs(self, show: bool) -> None:
        """Toggle format diff visibility."""
        self._show_format_diffs = show
        self.update()

    def set_show_badges(self, show: bool) -> None:
        """Toggle badge visibility."""
        self._show_badges = show
        self.update()

    def set_sync_selection(self, rect: QRectF | None) -> None:
        """Set synchronized selection overlay (shadow mask) for real-time feedback.

        This shows a semi-transparent overlay on the OTHER viewer when the user
        is selecting on one side, clearly indicating "the system will only look
        in this area".
        """
        self._sync_selection_overlay = rect
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Draw sync selection overlay - SIMPLIFIED: just a subtle border, not full shadow
        # This is less intrusive and doesn't block the view
        if self._sync_selection_overlay is not None:
            rect = self._sync_selection_overlay
            # Just draw a dashed border to indicate the sync area
            pen = QPen(QColor(52, 152, 219, 150))
            pen.setWidth(2)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)

            # Add a small label at top-left corner
            painter.setPen(QColor(52, 152, 219, 200))
            font = painter.font()
            font.setPointSize(8)
            font.setBold(True)
            painter.setFont(font)
            label_rect = QRectF(rect.left(), rect.top() - 14, 60, 12)
            painter.fillRect(label_rect, QColor(255, 255, 255, 200))
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "对应位置")

        # Draw normal overlays (lightweight background style)
        for rect, color, diff_type in self._overlays:
            # Filter out format diffs if disabled
            if diff_type == "format" and not self._show_format_diffs:
                continue

            # Light background fill only (no border for cleaner look)
            fill = QColor(color)
            fill.setAlpha(30)  # Very light background
            painter.fillRect(rect, fill)

            # Subtle left edge indicator (like Word track changes)
            edge_width = 3
            edge_rect = QRectF(rect.left(), rect.top(), edge_width, rect.height())
            edge_color = QColor(color)
            edge_color.setAlpha(180)
            painter.fillRect(edge_rect, edge_color)

        # Draw hover overlay (highlighted)
        if self._hover_overlay:
            rect, color = self._hover_overlay
            pen = QPen(color)
            pen.setWidth(2)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            fill = QColor(color)
            fill.setAlpha(40)
            painter.setBrush(fill)
            painter.drawRect(rect)

        # Draw selected overlays (bold border)
        for rect, color in self._selected_overlays:
            pen = QPen(color)
            pen.setWidth(2)
            painter.setPen(pen)
            fill = QColor(color)
            fill.setAlpha(35)
            painter.setBrush(fill)
            painter.drawRect(rect)

        # Draw badges (only if enabled)
        if self._show_badges:
            for rect, badge_text, badge_color in self._badges:
                # Skip format badges if format diffs are hidden
                if badge_text in {"格式", "格"} and not self._show_format_diffs:
                    continue

                # Smaller, more refined badge
                badge_size = 16
                badge_rect = QRectF(
                    rect.left() - 2,  # Left edge, not overlapping text
                    rect.top() - badge_size / 2,
                    badge_size,
                    badge_size,
                )

                # Draw badge with slight transparency
                badge_fill = QColor(badge_color)
                badge_fill.setAlpha(200)
                painter.setBrush(badge_fill)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(badge_rect)

                # Badge text
                painter.setPen(QColor(255, 255, 255))
                font = painter.font()
                font.setPointSize(7)
                font.setBold(True)
                painter.setFont(font)
                painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_text)


class SelectableImageLabel(OverlayImageLabel):
    """An overlay label that supports mouse-drag rectangle selection (temporary rect)."""

    selectionFinished = Signal(QRect)
    selectionChanged = Signal(QRect)  # Real-time selection feedback (during drag)
    zoomRequested = Signal(int)  # +1 (zoom in) / -1 (zoom out)

    # Tool modes
    MODE_PAN = "pan"  # Hand tool - drag to pan
    MODE_SELECT = "select"  # Selection tool - drag to select region

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setMouseTracking(True)
        self._origin: QPoint | None = None
        self._rect: QRect | None = None
        self._mode = self.MODE_SELECT  # Default to select mode
        self._is_panning = False
        self._pan_start: QPoint | None = None

        # Set initial cursor
        self._update_cursor()

    def set_mode(self, mode: str) -> None:
        """Set interaction mode (pan or select)."""
        self._mode = mode
        self._update_cursor()

    def _update_cursor(self) -> None:
        """Update cursor based on current mode."""
        if self._mode == self.MODE_PAN:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self.pixmap() is not None:
            if self._mode == self.MODE_PAN:
                # Pan mode: start panning
                self._is_panning = True
                self._pan_start = event.position().toPoint()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            else:
                # Select mode: start selection
                origin = event.position().toPoint()
                self._origin = origin
                self._rect = QRect(origin, origin)
                self.update()

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._is_panning and self._pan_start is not None:
            # Pan mode: scroll the parent scroll area
            scroll_area: QObject | None = self.parent()
            while scroll_area is not None and not isinstance(scroll_area, QScrollArea):
                scroll_area = scroll_area.parent()

            if isinstance(scroll_area, QScrollArea):
                current = event.position().toPoint()
                delta = self._pan_start - current

                h_bar = scroll_area.horizontalScrollBar()
                v_bar = scroll_area.verticalScrollBar()
                h_bar.setValue(h_bar.value() + delta.x())
                v_bar.setValue(v_bar.value() + delta.y())

                self._pan_start = current
        elif self._origin is not None:
            # Select mode: update selection rect
            self._rect = QRect(self._origin, event.position().toPoint()).normalized()
            # Emit real-time selection change for synchronized feedback
            self.selectionChanged.emit(self._rect)
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self._is_panning:
            # End panning
            self._is_panning = False
            self._pan_start = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            return

        if self._origin is None or self._rect is None:
            return

        rect = self._rect.normalized()
        self._origin = None
        self._rect = None
        self.update()

        # Ignore tiny drags.
        if rect.width() < 5 or rect.height() < 5:
            return
        self.selectionFinished.emit(rect)

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        # Ctrl + wheel zooms the current viewer without changing existing default wheel behavior.
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta != 0:
                self.zoomRequested.emit(1 if delta > 0 else -1)
            event.accept()
            return
        super().wheelEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        if self._rect is None:
            return

        painter = QPainter(self)
        # Selection rect styling
        pen = QPen(QColor(52, 152, 219, 220))
        pen.setWidth(2)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(QColor(52, 152, 219, 40))
        painter.drawRect(self._rect)


def render_pdf_page_to_pixmap(pdf_path: Path, page_number: int = 0, zoom: float = 2.0) -> QPixmap:
    doc = fitz.open(pdf_path)
    try:
        page = doc.load_page(page_number)
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=False)

        # Convert Pixmap -> QImage -> QPixmap
        img = QImage(
            pix.samples,
            pix.width,
            pix.height,
            pix.stride,
            QImage.Format.Format_RGB888,  # type: ignore[attr-defined]
        )
        img = img.copy()  # detach from PyMuPDF buffer
        return QPixmap.fromImage(img)
    finally:
        doc.close()


class PdfImageViewer(QWidget):
    selectionFinished = Signal(QRect)
    zoomRequested = Signal(int)
    uploadRequested = Signal()

    def __init__(self, title: str, *, selectable: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._title = QLabel(title)
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title.setStyleSheet("font-weight: 600;")

        self._empty_hint = QLabel("请上传PDF文档")
        self._empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_hint.setStyleSheet("color:#6b7280;font-size:13px;")
        self._upload_btn = QPushButton("上传PDF")
        self._upload_btn.setObjectName("uploadCard")
        self._upload_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._upload_btn.setMinimumSize(260, 150)
        self._upload_btn.clicked.connect(self.uploadRequested)

        empty_layout = QVBoxLayout()
        empty_layout.setContentsMargins(0, 0, 0, 0)
        empty_layout.setSpacing(10)
        empty_layout.addStretch(1)
        empty_layout.addWidget(self._upload_btn, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addWidget(self._empty_hint, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch(1)
        self._empty_view = QWidget()
        self._empty_view.setLayout(empty_layout)
        self._empty_view.setStyleSheet("background:#ffffff;")

        self._image: OverlayImageLabel
        if selectable:
            self._image = SelectableImageLabel("(drag to select)")
            self._image.selectionFinished.connect(self.selectionFinished)
            self._image.zoomRequested.connect(self.zoomRequested)
        else:
            self._image = OverlayImageLabel("(no document)")

        # Make the pixmap coordinate origin stable (top-left) for selection mapping.
        self._image.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        # PySide6: background role is an enum (not an attribute on QPalette instances).
        self._image.setBackgroundRole(QPalette.ColorRole.Base)
        # Keep pixel-precise rendering while allowing window to shrink on smaller screens.
        self._image.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self._image.setMinimumSize(0, 0)
        self._image.setScaledContents(False)

        self._scroll = QScrollArea()
        # Important: keep widget size equal to pixmap size so mouse coordinates map 1:1.
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._scroll.setMinimumSize(0, 0)
        self._scroll.setWidget(self._image)

        self._stack = QStackedLayout()
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.setSpacing(0)
        self._stack.addWidget(self._empty_view)
        self._stack.addWidget(self._scroll)
        self._stack.setCurrentWidget(self._empty_view)
        stack_host = QWidget()
        stack_host.setLayout(self._stack)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self._title)
        layout.addWidget(stack_host)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumWidth(260)

    def set_pixmap(self, pixmap: QPixmap) -> None:
        self._image.setPixmap(pixmap)
        self._image.adjustSize()
        if not pixmap.isNull():
            self._stack.setCurrentWidget(self._scroll)

    def set_title(self, title: str) -> None:
        self._title.setText(title)

    def set_empty_state(self, message: str, button_text: str) -> None:
        self._empty_hint.setText(message)
        self._upload_btn.setText(button_text)
        self._image.setPixmap(QPixmap())
        self._image.setText("")
        self._stack.setCurrentWidget(self._empty_view)

    def set_overlays(self, overlays: list[tuple[QRectF, QColor]]) -> None:
        if hasattr(self._image, "set_overlays"):
            self._image.set_overlays(overlays)  # type: ignore[attr-defined]

    def set_selected_overlays(self, overlays: list[tuple[QRectF, QColor]]) -> None:
        if hasattr(self._image, "set_selected_overlays"):
            self._image.set_selected_overlays(overlays)  # type: ignore[attr-defined]

    def set_hover_overlay(self, overlay: tuple[QRectF, QColor] | None) -> None:
        if hasattr(self._image, "set_hover_overlay"):
            self._image.set_hover_overlay(overlay)  # type: ignore[attr-defined]

    def set_badges(self, badges: list[tuple[QRectF, str, QColor]]) -> None:
        if hasattr(self._image, "set_badges"):
            self._image.set_badges(badges)  # type: ignore[attr-defined]

    def scroll_to_rect(self, rect: QRectF) -> None:
        """Scroll so that `rect` (in pixmap coordinates) is roughly centered."""

        if rect.isNull():
            return
        area = self._scroll
        hbar = area.horizontalScrollBar()
        vbar = area.verticalScrollBar()
        cx = int(rect.center().x())
        cy = int(rect.center().y())
        hbar.setValue(max(0, cx - area.viewport().width() // 2))
        vbar.setValue(max(0, cy - area.viewport().height() // 2))

    def set_error(self, message: str) -> None:
        self.set_empty_state(message, "上传PDF")


class BackgroundTaskRunner(QObject):
    """Run one blocking callable in a worker thread and emit result/error."""

    finished = Signal(object, object)  # (result, error)

    def __init__(self, fn, args: tuple, kwargs: dict) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    @Slot()
    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
            self.finished.emit(result, None)
        except Exception as e:
            self.finished.emit(None, e)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Verbatim - PDF文档比对工具 v1.0")
        self._debug_log_text = str(os.getenv("VERBATIM_DEBUG_LOG_TEXT", "0")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self._debug_spans = str(os.getenv("VERBATIM_DEBUG_SPANS", "0")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self._last_spans_render_log: dict[str, tuple[int, float, int, int]] = {}
        self._ocr_result_spans_meta: dict[tuple[str, int, tuple[int, int, int, int], str], dict] = {}
        self._last_left_spans_meta: dict | None = None
        self._last_right_spans_meta: dict | None = None

        # ============================================
        # Professional UI style system (v1.0)
        # Goal: rigorous, trustworthy, restrained.
        # Brand primary color: dark blue gray (#2c3e50)
        # Accent color: deep blue (#2980b9), only for primary actions
        # Neutral gray ramp: #ecf0f1, #bdc3c7, #7f8c8d
        # ============================================

        # Brand primary color: dark blue gray (#2c3e50)
        # Accent color: deep blue (#2980b9), reserved for primary actions
        # 涓€х伆闃讹細#ecf0f1, #bdc3c7, #7f8c8d

        # Accent color: deep blue (#2980b9), only for primary actions
        # Neutral gray ramp: #ecf0f1, #bdc3c7, #7f8c8d
        self.setStyleSheet("""
            /* Global typography: modern sans serif */
            QMainWindow {
                background-color: #f3f5f7;
                font-family: "Microsoft YaHei", "Segoe UI", "PingFang SC", sans-serif;
            }
            
            /* Button base style: consistent 32px height, 6px radius */
            /* Button base style: consistent 32px height, 6px radius */
            QPushButton {
                background-color: #eef2f6;
                color: #1f2933;
                border: 1px solid #cfd8e3;
                padding: 7px 14px;
                border-radius: 6px;
                font-size: 13px;
                min-height: 20px;
            }
            QPushButton:hover {
                background-color: #e3e9f0;
                border-color: #9fb0c4;
            }
            QPushButton:disabled {
                background-color: #f5f5f5;
                color: #bdc3c7;
                border-color: #e6eaf0;
            }
            QPushButton:checked {
                background-color: #d5dde6;
                border-color: #9fb0c4;
            }
            
            /* Primary action button: the only strong emphasis in the top bar */
            /* Primary action button */
            QPushButton.primary {
                background-color: #2980b9;
                color: white;
                border: none;
                font-weight: 600;
                padding: 8px 24px;
            }
            QPushButton.primary:hover {
                background-color: #1f6dad;
            }
            QPushButton.primary:disabled {
                background-color: #bdc3c7;
                color: #ecf0f1;
            }
            
            /* 宸ュ叿鎸夐挳 */
            QToolButton {
                background-color: #eef2f6;
                color: #1f2933;
                border: 1px solid #cfd8e3;
                padding: 6px 12px;
                border-radius: 6px;
                font-size: 13px;
            }
            QToolButton:hover {
                background-color: #e3e9f0;
                border-color: #9fb0c4;
            }
            QToolButton:checked {
                background-color: #0f6cbd;
                color: white;
                border-color: #0a5a9f;
            }
            QComboBox#toolModeCombo {
                background-color: #ffffff;
                border: 1px solid #9fb0c4;
                border-radius: 6px;
                min-width: 120px;
                min-height: 28px;
                font-weight: 600;
                padding: 4px 10px;
            }
            QWidget#panelCtrlGroup {
                background-color: #f7f9fc;
                border: 1px solid #d4dde8;
                border-radius: 8px;
            }
            QPushButton#panelCtrlBtn {
                background-color: #ffffff;
                border: 1px solid #c5d0dd;
                border-radius: 6px;
                min-height: 26px;
                padding: 4px 10px;
            }
            QPushButton#panelCtrlBtn:hover {
                background-color: #f3f7fc;
                border-color: #8ea3bc;
            }
            QPushButton#panelCtrlBtn:disabled {
                background-color: #f3f5f8;
                color: #b8c2cd;
                border-color: #dfe5ec;
            }
            QComboBox#panelPageCombo {
                background-color: #ffffff;
                border: 1px solid #c5d0dd;
                border-radius: 6px;
                min-height: 26px;
                padding: 4px 8px;
            }
            QLabel#panelMetaLabel {
                color: #475569;
                font-size: 12px;
                font-weight: 600;
                padding: 0 2px;
            }
            QPushButton#uploadCard {
                background-color: #f5f7fa;
                color: #1f2933;
                border: 1px solid #d5dce5;
                border-radius: 12px;
                font-size: 16px;
                font-weight: 600;
                padding: 16px 20px;
            }
            QPushButton#uploadCard:hover {
                background-color: #f0f5ff;
                border: 2px solid #3b82f6;
                color: #1e3a8a;
            }
            QPushButton#uploadCard:pressed {
                background-color: #dbeafe;
                border: 2px solid #1d4ed8;
                color: #1e3a8a;
            }
            
            /* 鏍囩 */
            QLabel {
                color: #2c3e50;
                font-size: 13px;
            }
            QLabel.section-title {
                font-size: 14px;
                font-weight: 600;
                color: #2c3e50;
                padding: 8px 0;
                border-bottom: 1px solid #e8e8e8;
            }
            
            /* 鍒楄〃 */
            QListWidget {
                background-color: white;
                border: 1px solid #dde3e8;
                border-radius: 6px;
                padding: 4px;
            }
            QListWidget::item {
                padding: 8px 6px;
                border-radius: 3px;
            }
            QListWidget::item:selected {
                background-color: #e9f2fb;
                color: #1f2933;
            }
            QListWidget::item:hover {
                background-color: #f5f7f8;
            }
            
            /* 鏂囨湰缂栬緫 */
            QTextEdit {
                background-color: white;
                border: 1px solid #dde3e8;
                border-radius: 6px;
                padding: 8px;
                font-size: 13px;
                line-height: 1.5;
            }
            
            /* Combo box */
            QComboBox {
                background-color: white;
                border: 1px solid #d0d7de;
                border-radius: 6px;
                padding: 5px 10px;
                min-width: 80px;
                font-size: 13px;
            }
            QComboBox:drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background-color: white;
                border: 1px solid #dce1e4;
                selection-background-color: #e8f4fc;
                selection-color: #2c3e50;
            }
            
            /* Tab widget */
            QTabWidget::pane {
                border: 1px solid #dde3e8;
                border-radius: 6px;
                background: white;
            }
            QTabBar::tab {
                background: #f5f7f8;
                padding: 8px 16px;
                margin-right: 2px;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                font-size: 13px;
            }
            QTabBar::tab:selected {
                background: white;
                border-bottom: 2px solid #2980b9;
                color: #2c3e50;
            }
            
            /* 婊氬姩鍖哄煙 */
            QScrollArea {
                border: none;
                background-color: white;
            }
            
            /* Splitter */
            QSplitter::handle {
                background-color: #e8e8e8;
            }
            
            /* 鍗＄墖瀹瑰櫒 */
            .card {
                background-color: white;
                border: 1px solid #e8e8e8;
                border-radius: 6px;
            }
        """)

        self._left_pdf: Path | None = None
        self._right_pdf: Path | None = None
        self._left_page_number = 0
        self._right_page_number = 0
        self._left_page_count = 0
        self._right_page_count = 0
        self._left_committed_page_number = 0
        self._right_committed_page_number = 0
        self._left_nav_epoch = 0
        self._right_nav_epoch = 0
        self._left_nav_loading = False
        self._right_nav_loading = False
        self._base_render_zoom = RENDER_ZOOM
        self._left_zoom_ratio = 1.0
        self._right_zoom_ratio = 1.0

        # Tool mode state
        self._current_tool_mode = "select"  # "pan" or "select"

        # Format diff filter state - DEFAULT TO HIDDEN (ignore format diffs)
        self._show_format_diffs = False

        self._left_page = None
        self._right_page = None

        # Region selection (PDF coordinate bboxes) - user-driven on both sides.
        self._left_sel_bbox: BBox | None = None
        self._right_sel_bbox: BBox | None = None
        self._ocr_config_path = default_ocr_config_path()
        self._ocr_cfg = OcrConfig.load(self._ocr_config_path)
        self._ocr_client: PaddleOcrClient | None = None
        self._local_ocr_engine: LocalPaddleEngine | None = None
        self._local_ocr_json_engine: LocalPaddleOcrJsonEngine | None = None
        self._local_ocr_self_check: LocalOcrSelfCheck | None = None
        self._local_ocr_self_check_key: tuple[str, str, bool, str] | None = None
        self._last_ocr_note = ""
        self._last_ocr_errors: list[str] = []
        self._last_ocr_error_codes: list[str] = []
        self._last_ocr_state = OcrRunState.BLOCKED
        self._last_ocr_state_reason = "unknown"
        self._ocr_result_cache: dict[tuple[str, int, tuple[int, int, int, int], str], OcrResult] = {}
        self._ocr_result_spans_cache: dict[
            tuple[str, int, tuple[int, int, int, int], str],
            list[OcrSpan],
        ] = {}
        self._last_pure_content_mode = False
        self._last_compare_summary = "等待比对结果"
        self._last_compare_decision_status = "PASS"
        self._last_decision_basis = "text"
        self._last_gate_reason = ""
        self._last_fallback_reason = ""
        self._last_compare_vm: CompareViewModel | None = None
        self._trace_id = ""
        self._is_comparing = False
        self._last_compare_debug_state: tuple[bool, bool, bool, bool, bool] | None = None
        self._last_diff_ops: list[DiffOp] = []
        self._last_left_region: RegionData | None = None
        self._last_right_region: RegionData | None = None
        self._last_left_spans: list[OcrSpan] = []
        self._last_right_spans: list[OcrSpan] = []
        self._last_quality_warnings: list[str] = []
        self._last_left_ocr_applied = False
        self._last_right_ocr_applied = False
        self._last_left_ocr_has_coords = False
        self._last_right_ocr_has_coords = False
        self._last_left_coords_reliable = True
        self._last_right_coords_reliable = True
        self._focused_diff_op: DiffOp | None = None
        # Keep OCR/network waits bounded for interactive UX.
        self._bg_task_timeout_ms = int(os.getenv("VERBATIM_BG_TASK_TIMEOUT_MS", "25000") or "25000")
        self._compact_ui = False
        self._left_doc_profile: DocumentProfile | None = None
        self._right_doc_profile: DocumentProfile | None = None
        self._prealign_active = False
        self._prealign_manual_adjust_steps = 0
        self._prealign_base_left_bbox: BBox | None = None
        self._prealign_base_right_bbox: BBox | None = None
        self._prealign_last_left_bbox: BBox | None = None
        self._prealign_last_right_bbox: BBox | None = None
        self._compare_input_state: dict[str, bool] = {}
        self._left_force_ocr = False
        self._right_force_ocr = False
        self._left_doc_quality_note = ""
        self._right_doc_quality_note = ""
        self._auto_ocr_enabled = True
        self._ocr_prefer_cloud = True
        self._local_ocr_fail_streak = 0
        self._local_ocr_cooldown_until = 0.0
        self._local_ocr_breaker_warned = False
        self._local_ocr_fail_threshold = max(1, int(os.getenv("VERBATIM_LOCAL_OCR_FAIL_THRESHOLD", "3") or "3"))
        self._local_ocr_cooldown_sec = max(5, int(os.getenv("VERBATIM_LOCAL_OCR_COOLDOWN_SEC", "180") or "180"))
        self._manual_review_gate_required = str(os.getenv("VERBATIM_MANUAL_REVIEW_REQUIRED", "1")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

        # ============================================
        # Top control bar: three logical sections
        # Section 1: document controls | Section 2: normalization controls | Section 3: compare actions
        # ============================================

        # Top control bar: three logical sections
        # Section 1: document controls | Section 2: normalization controls | Section 3: compare actions
        controls = QWidget()
        controls.setStyleSheet("background-color: white; border-bottom: 1px solid #dde3e8;")
        controls_layout = QHBoxLayout(controls)
        controls_layout.setContentsMargins(12, 8, 12, 8)
        controls_layout.setSpacing(8)

        # ===== Section 1: document controls =====
        # ===== Section 1: document controls =====
        doc_control_group = QWidget()
        doc_control_layout = QHBoxLayout(doc_control_group)
        doc_control_layout.setContentsMargins(0, 0, 0, 0)
        doc_control_layout.setSpacing(8)

        self._tool_mode_label = QLabel("工具模式")
        self._tool_mode_label.setStyleSheet("font-size:12px;color:#5b6775;font-weight:600;")
        self._tool_mode_combo = QComboBox()
        self._tool_mode_combo.setObjectName("toolModeCombo")
        self._tool_mode_combo.addItem("框选", "select")
        self._tool_mode_combo.addItem("平移", "pan")
        self._tool_mode_combo.setToolTip("当前操作模式")
        self._tool_mode_combo.currentIndexChanged.connect(self._on_tool_mode_changed)
        doc_control_layout.addWidget(self._tool_mode_label)
        doc_control_layout.addWidget(self._tool_mode_combo)
        doc_control_layout.addSpacing(4)

        # 宸︿晶椤电爜瀵艰埅
        left_nav_widget = QWidget()
        left_nav_layout = QHBoxLayout(left_nav_widget)
        left_nav_layout.setContentsMargins(0, 0, 0, 0)
        left_nav_layout.setSpacing(4)

        left_page_group = QWidget()
        left_page_group.setObjectName("panelCtrlGroup")
        left_page_layout = QHBoxLayout(left_page_group)
        left_page_layout.setContentsMargins(6, 4, 6, 4)
        left_page_layout.setSpacing(4)

        self._btn_left_prev = QPushButton("上一页")
        self._btn_left_prev.setObjectName("panelCtrlBtn")
        self._btn_left_prev.setMinimumWidth(52)
        self._btn_left_prev.setEnabled(False)
        self._btn_left_prev.setToolTip("左侧上一页")
        self._btn_left_prev.clicked.connect(self._left_prev_page)
        self._left_page_combo = QComboBox()
        self._left_page_combo.setObjectName("panelPageCombo")
        self._left_page_combo.setMinimumWidth(78)
        self._left_page_combo.setEnabled(False)
        self._left_page_combo.currentIndexChanged.connect(self._left_page_combo_changed)
        self._left_page_label = QLabel("共 0 页")
        self._left_page_label.setObjectName("panelMetaLabel")
        self._btn_left_next = QPushButton("下一页")
        self._btn_left_next.setObjectName("panelCtrlBtn")
        self._btn_left_next.setMinimumWidth(52)
        self._btn_left_next.setEnabled(False)
        self._btn_left_next.setToolTip("左侧下一页")
        self._btn_left_next.clicked.connect(self._left_next_page)
        left_page_layout.addWidget(self._btn_left_prev)
        left_page_layout.addWidget(self._left_page_combo)
        left_page_layout.addWidget(self._left_page_label)
        left_page_layout.addWidget(self._btn_left_next)

        left_zoom_group = QWidget()
        left_zoom_group.setObjectName("panelCtrlGroup")
        left_zoom_layout = QHBoxLayout(left_zoom_group)
        left_zoom_layout.setContentsMargins(6, 4, 6, 4)
        left_zoom_layout.setSpacing(4)

        self._btn_left_zoom_out = QPushButton("-")
        self._btn_left_zoom_out.setObjectName("panelCtrlBtn")
        self._btn_left_zoom_out.setMinimumWidth(28)
        self._btn_left_zoom_out.setToolTip("左侧缩小")
        self._btn_left_zoom_out.clicked.connect(lambda: self._adjust_zoom("left", -ZOOM_STEP))
        self._left_zoom_label = QLabel("100%")
        self._left_zoom_label.setObjectName("panelMetaLabel")
        self._left_zoom_label.setMinimumWidth(48)
        self._left_zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._btn_left_zoom_in = QPushButton("+")
        self._btn_left_zoom_in.setObjectName("panelCtrlBtn")
        self._btn_left_zoom_in.setMinimumWidth(28)
        self._btn_left_zoom_in.setToolTip("左侧放大")
        self._btn_left_zoom_in.clicked.connect(lambda: self._adjust_zoom("left", ZOOM_STEP))
        self._btn_left_zoom_reset = QPushButton("100%")
        self._btn_left_zoom_reset.setObjectName("panelCtrlBtn")
        self._btn_left_zoom_reset.setMinimumWidth(48)
        self._btn_left_zoom_reset.setToolTip("左侧重置为100%")
        self._btn_left_zoom_reset.clicked.connect(lambda: self._set_zoom_ratio("left", 1.0))
        left_zoom_layout.addWidget(self._btn_left_zoom_out)
        left_zoom_layout.addWidget(self._left_zoom_label)
        left_zoom_layout.addWidget(self._btn_left_zoom_in)
        left_zoom_layout.addWidget(self._btn_left_zoom_reset)

        left_nav_layout.addWidget(left_page_group)
        left_nav_layout.addWidget(left_zoom_group)

        # 鍙充晶椤电爜瀵艰埅
        right_nav_widget = QWidget()
        right_nav_layout = QHBoxLayout(right_nav_widget)
        right_nav_layout.setContentsMargins(0, 0, 0, 0)
        right_nav_layout.setSpacing(4)

        right_page_group = QWidget()
        right_page_group.setObjectName("panelCtrlGroup")
        right_page_layout = QHBoxLayout(right_page_group)
        right_page_layout.setContentsMargins(6, 4, 6, 4)
        right_page_layout.setSpacing(4)

        self._btn_right_prev = QPushButton("上一页")
        self._btn_right_prev.setObjectName("panelCtrlBtn")
        self._btn_right_prev.setMinimumWidth(52)
        self._btn_right_prev.setEnabled(False)
        self._btn_right_prev.setToolTip("右侧上一页")
        self._btn_right_prev.clicked.connect(self._right_prev_page)
        self._right_page_combo = QComboBox()
        self._right_page_combo.setObjectName("panelPageCombo")
        self._right_page_combo.setMinimumWidth(78)
        self._right_page_combo.setEnabled(False)
        self._right_page_combo.currentIndexChanged.connect(self._right_page_combo_changed)
        self._right_page_label = QLabel("共 0 页")
        self._right_page_label.setObjectName("panelMetaLabel")
        self._btn_right_next = QPushButton("下一页")
        self._btn_right_next.setObjectName("panelCtrlBtn")
        self._btn_right_next.setMinimumWidth(52)
        self._btn_right_next.setEnabled(False)
        self._btn_right_next.setToolTip("右侧下一页")
        self._btn_right_next.clicked.connect(self._right_next_page)
        right_page_layout.addWidget(self._btn_right_prev)
        right_page_layout.addWidget(self._right_page_combo)
        right_page_layout.addWidget(self._right_page_label)
        right_page_layout.addWidget(self._btn_right_next)

        right_zoom_group = QWidget()
        right_zoom_group.setObjectName("panelCtrlGroup")
        right_zoom_layout = QHBoxLayout(right_zoom_group)
        right_zoom_layout.setContentsMargins(6, 4, 6, 4)
        right_zoom_layout.setSpacing(4)

        self._btn_right_zoom_out = QPushButton("-")
        self._btn_right_zoom_out.setObjectName("panelCtrlBtn")
        self._btn_right_zoom_out.setMinimumWidth(28)
        self._btn_right_zoom_out.setToolTip("右侧缩小")
        self._btn_right_zoom_out.clicked.connect(lambda: self._adjust_zoom("right", -ZOOM_STEP))
        self._right_zoom_label = QLabel("100%")
        self._right_zoom_label.setObjectName("panelMetaLabel")
        self._right_zoom_label.setMinimumWidth(48)
        self._right_zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._btn_right_zoom_in = QPushButton("+")
        self._btn_right_zoom_in.setObjectName("panelCtrlBtn")
        self._btn_right_zoom_in.setMinimumWidth(28)
        self._btn_right_zoom_in.setToolTip("右侧放大")
        self._btn_right_zoom_in.clicked.connect(lambda: self._adjust_zoom("right", ZOOM_STEP))
        self._btn_right_zoom_reset = QPushButton("100%")
        self._btn_right_zoom_reset.setObjectName("panelCtrlBtn")
        self._btn_right_zoom_reset.setMinimumWidth(48)
        self._btn_right_zoom_reset.setToolTip("右侧重置为100%")
        self._btn_right_zoom_reset.clicked.connect(lambda: self._set_zoom_ratio("right", 1.0))
        right_zoom_layout.addWidget(self._btn_right_zoom_out)
        right_zoom_layout.addWidget(self._right_zoom_label)
        right_zoom_layout.addWidget(self._btn_right_zoom_in)
        right_zoom_layout.addWidget(self._btn_right_zoom_reset)

        right_nav_layout.addWidget(right_page_group)
        right_nav_layout.addWidget(right_zoom_group)

        doc_control_layout.addStretch(1)

        # ===== Section 2: normalization controls =====
        norm_control_group = QWidget()
        norm_control_layout = QHBoxLayout(norm_control_group)
        norm_control_layout.setContentsMargins(0, 0, 0, 0)
        norm_control_layout.setSpacing(6)

        # Format diff visibility toggle
        self._btn_filter_format = QPushButton("格式差异")
        self._btn_filter_format.setCheckable(True)
        self._btn_filter_format.setChecked(False)
        self._btn_filter_format.setToolTip("显示/隐藏格式差异（字体、字号等）")
        self._btn_filter_format.clicked.connect(self._toggle_format_diffs)

        # Pure content mode
        self._btn_pure_content = QPushButton("忽略空格")
        self._btn_pure_content.setCheckable(True)
        self._btn_pure_content.setChecked(True)
        self._btn_pure_content.setToolTip("忽略所有空格和换行")
        self._btn_pure_content.clicked.connect(self._on_pure_content_toggled)

        # Normalization options kept as compact controls
        self._reading_order_combo = QComboBox()
        self._reading_order_combo.addItem("顺序: 自动", "auto")
        self._reading_order_combo.addItem("顺序: 原始", "raw")
        self._reading_order_combo.addItem("顺序: 单栏", "single_column")
        self._reading_order_combo.addItem("顺序: 双栏", "two_column")
        self._reading_order_combo.setToolTip("提取文本顺序模式")
        self._reading_order_combo.setMinimumWidth(110)

        self._btn_ignore_punctuation = QPushButton("忽略标点")
        self._btn_ignore_punctuation.setCheckable(True)
        self._btn_ignore_punctuation.setChecked(False)
        self._btn_ignore_punctuation.setToolTip("忽略标点符号差异")
        self._btn_ignore_punctuation.setStyleSheet("font-size: 12px; padding: 5px 10px;")

        self._btn_normalize_numbers = QPushButton("数字标准")
        self._btn_normalize_numbers.setCheckable(True)
        self._btn_normalize_numbers.setChecked(False)
        self._btn_normalize_numbers.setToolTip("删除数字中的千分位逗号")
        self._btn_normalize_numbers.setStyleSheet("font-size: 12px; padding: 5px 10px;")

        self._btn_merge_keyvalue = QPushButton("键值合并")
        self._btn_merge_keyvalue.setCheckable(True)
        self._btn_merge_keyvalue.setChecked(True)
        self._btn_merge_keyvalue.setToolTip("合并以冒号结尾的断行字段")
        self._btn_merge_keyvalue.setStyleSheet("font-size: 12px; padding: 5px 10px;")

        self._btn_use_ocr = QPushButton("自动OCR回退")
        self._btn_use_ocr.setCheckable(True)
        self._btn_use_ocr.setChecked(True)
        self._btn_use_ocr.toggled.connect(lambda checked: setattr(self, "_auto_ocr_enabled", bool(checked)))
        self._btn_use_ocr.setToolTip("检测到疑似乱码/低质量文本层时，自动调用OCR回退")
        self._btn_use_ocr.setStyleSheet("font-size: 12px; padding: 5px 10px;")
        self._btn_use_ocr.setVisible(False)
        self._btn_dual_ocr = QPushButton("高噪声双侧OCR")
        self._btn_dual_ocr.setCheckable(True)
        self._btn_dual_ocr.setChecked(False)
        self._btn_dual_ocr.setToolTip("当任一侧触发OCR时，强制两侧都使用OCR（降低体系差异噪声）")
        self._btn_dual_ocr.setStyleSheet("font-size: 12px; padding: 5px 10px;")

        self._ocr_mode_combo = QComboBox()
        self._ocr_mode_combo.addItem("OCR: 同步", "sync")
        self._ocr_mode_combo.addItem("OCR: 异步", "async")
        self._ocr_mode_combo.setToolTip("同步适合当前交互式框选；异步适合后续批量任务")
        self._ocr_mode_combo.setMinimumWidth(100)
        self._ocr_mode_combo.setEnabled(bool(self._ocr_cfg))

        self._btn_ocr_settings = QPushButton("OCR设置")
        self._btn_ocr_settings.setToolTip("设置OCR Token与接口地址")
        self._btn_ocr_settings.clicked.connect(self._open_ocr_settings)
        self._btn_ocr_diag = QPushButton("诊断")
        self._btn_ocr_diag.setToolTip("一键导出OCR诊断信息")
        self._btn_ocr_diag.clicked.connect(self._open_diagnostics)
        self._ocr_token_status = QLabel("OCR Token: ● 未配置")
        self._ocr_token_status.setStyleSheet("color:#c0392b;font-size:12px;font-weight:600;")

        self._btn_advanced_settings = QPushButton("高级设置")
        self._btn_advanced_settings.setToolTip("打开高级比对选项")
        self._btn_advanced_settings.clicked.connect(self._open_advanced_settings)
        self._btn_prealign = QPushButton("预对齐建议")
        self._btn_prealign.setToolTip("机器预对齐候选：先看建议，再一键应用选区")
        self._btn_prealign.clicked.connect(self._open_prealign_suggestions)

        # Keep high-frequency controls on top bar; move secondary knobs to Advanced Settings.
        norm_control_layout.addWidget(self._btn_prealign)
        norm_control_layout.addWidget(self._btn_ocr_settings)
        norm_control_layout.addWidget(self._btn_ocr_diag)
        norm_control_layout.addWidget(self._btn_advanced_settings)
        norm_control_layout.addStretch(1)

        # ===== Section 3: compare actions =====
        # ===== Section 3: compare actions =====
        compare_control_group = QWidget()
        compare_control_layout = QHBoxLayout(compare_control_group)
        compare_control_layout.setContentsMargins(0, 0, 0, 0)
        compare_control_layout.setSpacing(8)

        # Primary compare action
        # Primary compare action
        self._btn_compare = QPushButton("开始对比")
        self._btn_compare.setProperty("class", "primary")
        self._btn_compare.setStyleSheet("""
            QPushButton {
                background-color: #2980b9;
                color: white;
                border: none;
                font-weight: 600;
                font-size: 14px;
                padding: 8px 24px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #1f6dad;
            }
            QPushButton:disabled {
                background-color: #bdc3c7;
                color: #ecf0f1;
            }
        """)
        self._btn_compare.setEnabled(False)
        self._btn_compare.clicked.connect(self._on_compare_clicked)

        # Save selection action uses a neutral style
        self._btn_save_selection = QPushButton("保存选区")
        self._btn_save_selection.setEnabled(False)
        self._btn_save_selection.clicked.connect(self._save_current_selection)

        compare_control_layout.addWidget(self._btn_compare)
        compare_control_layout.addWidget(self._btn_save_selection)

        # Single-row top bar for cleaner, denser control area.
        controls_layout.addWidget(doc_control_group, 0)
        controls_layout.addStretch(1)
        controls_layout.addWidget(norm_control_group)
        controls_layout.addWidget(compare_control_group)

        # ============================================
        # Right panel uses stacked cards for history, diffs, and details
        # ============================================

        right_panel = QWidget()
        right_panel.setMinimumWidth(260)
        right_panel.setMaximumWidth(420)
        right_panel.setStyleSheet("""
            QWidget {
                background-color: #f8f9fa;
            }
        """)
        right_panel_layout = QVBoxLayout(right_panel)
        right_panel_layout.setContentsMargins(12, 12, 12, 12)
        right_panel_layout.setSpacing(12)

        # ===== Card 1: selection history =====
        history_card = QWidget()
        history_card.setStyleSheet("""
            QWidget {
                background-color: white;
                border: 1px solid #dde3e8;
                border-radius: 6px;
            }
        """)
        history_card_layout = QVBoxLayout(history_card)
        history_card_layout.setContentsMargins(12, 12, 12, 12)
        history_card_layout.setSpacing(8)

        history_title = QLabel("选区历史")
        history_title.setStyleSheet("""
            font-size: 14px;
            font-weight: 600;
            color: #2c3e50;
            padding-bottom: 8px;
                border-bottom: 1px solid #dde3e8;
        """)
        history_card_layout.addWidget(history_title)

        self._region_list = QListWidget()
        self._region_list.setMinimumWidth(200)
        self._region_list.setMaximumWidth(320)
        self._region_list.itemClicked.connect(self._on_region_item_clicked)
        history_card_layout.addWidget(self._region_list)

        # Delete selection action
        self._btn_delete_region = QPushButton("删除选区")
        self._btn_delete_region.clicked.connect(self._delete_selected_region)
        self._btn_delete_region.setEnabled(False)
        self._btn_delete_region.setStyleSheet("font-size: 12px;")
        history_card_layout.addWidget(self._btn_delete_region)

        right_panel_layout.addWidget(history_card)

        # ===== 卡片2：比对历史（可回放） =====
        compare_history_card = QWidget()
        compare_history_card.setStyleSheet("""
            QWidget {
                background-color: white;
                border: 1px solid #dde3e8;
                border-radius: 6px;
            }
        """)
        compare_history_layout = QVBoxLayout(compare_history_card)
        compare_history_layout.setContentsMargins(12, 12, 12, 12)
        compare_history_layout.setSpacing(8)

        compare_history_title = QLabel("比对历史")
        compare_history_title.setStyleSheet("""
            font-size: 14px;
            font-weight: 600;
            color: #2c3e50;
            padding-bottom: 8px;
            border-bottom: 1px solid #dde3e8;
        """)
        compare_history_layout.addWidget(compare_history_title)

        self._compare_history_list = QListWidget()
        self._compare_history_list.setMinimumWidth(200)
        self._compare_history_list.setMaximumWidth(320)
        self._compare_history_list.itemClicked.connect(self._on_compare_history_item_clicked)
        compare_history_layout.addWidget(self._compare_history_list)

        right_panel_layout.addWidget(compare_history_card)

        # ===== Card 3: diff list =====
        diff_card = QWidget()
        diff_card.setStyleSheet("""
            QWidget {
                background-color: white;
                border: 1px solid #dde3e8;
                border-radius: 6px;
            }
        """)
        diff_card_layout = QVBoxLayout(diff_card)
        diff_card_layout.setContentsMargins(12, 12, 12, 12)
        diff_card_layout.setSpacing(8)

        diff_title = QLabel("差异列表")
        diff_title.setStyleSheet("""
            font-size: 14px;
            font-weight: 600;
            color: #2c3e50;
            padding-bottom: 8px;
            border-bottom: 1px solid #e8e8e8;
        """)
        diff_card_layout.addWidget(diff_title)

        # Diff tabs
        diff_tab_widget = QTabWidget()

        # Content diff tab
        content_diff_widget = QWidget()
        content_diff_layout = QVBoxLayout(content_diff_widget)
        content_diff_layout.setContentsMargins(0, 0, 0, 0)

        self._content_diff_list = QListWidget()
        self._content_diff_list.setStyleSheet("border: none;")
        self._content_diff_list.itemClicked.connect(self._on_diff_item_clicked)
        content_diff_layout.addWidget(self._content_diff_list)

        # Format diff tab
        format_diff_widget = QWidget()
        format_diff_layout = QVBoxLayout(format_diff_widget)
        format_diff_layout.setContentsMargins(0, 0, 0, 0)

        self._format_diff_list = QListWidget()
        self._format_diff_list.setStyleSheet("border: none;")
        self._format_diff_list.itemClicked.connect(self._on_diff_item_clicked)
        format_diff_layout.addWidget(self._format_diff_list)

        diff_tab_widget.addTab(content_diff_widget, "内容差异 (0)")
        diff_tab_widget.addTab(format_diff_widget, "格式差异 (0)")

        diff_card_layout.addWidget(diff_tab_widget)
        self._diff_tab_widget = diff_tab_widget

        right_panel_layout.addWidget(diff_card)

        # ===== Card 4: diff details =====
        details_card = QWidget()
        details_card.setStyleSheet("""
            QWidget {
                background-color: white;
                border: 1px solid #e8e8e8;
                border-radius: 6px;
            }
        """)
        details_card_layout = QVBoxLayout(details_card)
        details_card_layout.setContentsMargins(12, 12, 12, 12)
        details_card_layout.setSpacing(8)

        details_title = QLabel("差异详情")
        details_title.setStyleSheet("""
            font-size: 14px;
            font-weight: 600;
            color: #2c3e50;
            padding-bottom: 8px;
            border-bottom: 1px solid #e8e8e8;
        """)
        details_card_layout.addWidget(details_title)

        self._diff_details = QTextEdit()
        self._diff_details.setReadOnly(True)
        self._diff_details.setMinimumHeight(150)
        self._diff_details.setStyleSheet("border: none; background-color: #fafbfc;")
        details_card_layout.addWidget(self._diff_details)

        right_panel_layout.addWidget(details_card)

        # ============================================
        # Main layout: left PDF | right PDF | right-side panel
        # ============================================
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.setChildrenCollapsible(False)
        main_splitter.setStyleSheet("QSplitter::handle { background-color: #e8e8e8; }")

        # Left PDF viewer with framed surface
        self.left_view = PdfImageViewer("左侧文档 - 请先加载PDF", selectable=True)
        self.left_view.setStyleSheet("""
            PdfImageViewer {
                background-color: white;
                border: none;
            }
        """)

        # Right PDF viewer with framed surface
        self.right_view = PdfImageViewer("右侧文档 - 请先加载PDF", selectable=True)
        self.right_view.setStyleSheet("""
            PdfImageViewer {
                background-color: white;
                border: none;
            }
        """)

        # 璁剧疆鍒濆宸ュ叿妯″紡
        self._apply_tool_mode_to_viewers()

        left_card = QWidget()
        left_card.setStyleSheet("background:#ffffff;border:1px solid #dde3e8;border-radius:8px;")
        left_card_layout = QVBoxLayout(left_card)
        left_card_layout.setContentsMargins(10, 10, 10, 10)
        left_card_layout.setSpacing(8)
        left_header = QWidget()
        left_header.setStyleSheet("border:none;background:transparent;")
        left_header_layout = QVBoxLayout(left_header)
        left_header_layout.setContentsMargins(0, 0, 0, 0)
        left_header_layout.setSpacing(6)
        left_header_title = QLabel("左侧文档控制")
        left_header_title.setStyleSheet("font-size:12px;color:#5b6775;font-weight:600;")
        left_title_row = QWidget()
        left_title_row_layout = QHBoxLayout(left_title_row)
        left_title_row_layout.setContentsMargins(0, 0, 0, 0)
        left_title_row_layout.addWidget(left_header_title)
        left_title_row_layout.addStretch(1)
        left_header_layout.addWidget(left_title_row)
        left_header_layout.addWidget(left_nav_widget)
        left_card_layout.addWidget(left_header)
        left_card_layout.addWidget(self.left_view)

        right_card = QWidget()
        right_card.setStyleSheet("background:#ffffff;border:1px solid #dde3e8;border-radius:8px;")
        right_card_layout = QVBoxLayout(right_card)
        right_card_layout.setContentsMargins(10, 10, 10, 10)
        right_card_layout.setSpacing(8)
        right_header = QWidget()
        right_header.setStyleSheet("border:none;background:transparent;")
        right_header_layout = QVBoxLayout(right_header)
        right_header_layout.setContentsMargins(0, 0, 0, 0)
        right_header_layout.setSpacing(6)
        right_header_title = QLabel("右侧文档控制")
        right_header_title.setStyleSheet("font-size:12px;color:#5b6775;font-weight:600;")
        right_title_row = QWidget()
        right_title_row_layout = QHBoxLayout(right_title_row)
        right_title_row_layout.setContentsMargins(0, 0, 0, 0)
        right_title_row_layout.addWidget(right_header_title)
        right_title_row_layout.addStretch(1)
        right_header_layout.addWidget(right_title_row)
        right_header_layout.addWidget(right_nav_widget)
        right_card_layout.addWidget(right_header)
        right_card_layout.addWidget(self.right_view)

        main_splitter.addWidget(left_card)
        main_splitter.addWidget(right_card)
        main_splitter.addWidget(right_panel)
        main_splitter.setCollapsible(0, False)
        main_splitter.setCollapsible(1, False)
        main_splitter.setCollapsible(2, False)
        main_splitter.setStretchFactor(0, 4)
        main_splitter.setStretchFactor(1, 4)
        main_splitter.setStretchFactor(2, 2)
        main_splitter.setSizes([720, 720, 300])

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(controls)
        self._result_summary_bar = QLabel("结果摘要：等待比对结果")
        self._result_summary_bar.setStyleSheet(
            "background:#f7f9fb;color:#34495e;border-bottom:1px solid #dde3e8;padding:6px 12px;font-size:12px;"
        )
        root_layout.addWidget(self._result_summary_bar)
        root_layout.addWidget(main_splitter)
        self.setCentralWidget(root)

        # Initial empty state
        self.left_view.set_empty_state("左侧独立文档上传区", "上传左侧文档")
        self.right_view.set_empty_state("右侧独立文档上传区", "上传右侧文档")
        self._populate_diff_list([])
        self._update_compare_history_list()

        # Phase 15 model: user selects both regions, then clicks Compare.
        self.left_view.uploadRequested.connect(self._choose_left_pdf)
        self.right_view.uploadRequested.connect(self._choose_right_pdf)
        self.left_view.selectionFinished.connect(self._on_left_selection_finished)
        self.right_view.selectionFinished.connect(self._on_right_selection_finished)
        self.left_view.zoomRequested.connect(lambda d: self._adjust_zoom("left", ZOOM_STEP * float(d)))
        self.right_view.zoomRequested.connect(lambda d: self._adjust_zoom("right", ZOOM_STEP * float(d)))

        # Real-time synchronized selection feedback
        if hasattr(self.left_view._image, "selectionChanged"):
            self.left_view._image.selectionChanged.connect(self._on_left_selection_changing)  # type: ignore[attr-defined]
        if hasattr(self.right_view._image, "selectionChanged"):
            self.right_view._image.selectionChanged.connect(self._on_right_selection_changing)  # type: ignore[attr-defined]
        # Pages will be parsed once both PDFs are selected.

        self._apply_clean_ui_texts()
        self._refresh_zoom_ui()
        self._apply_responsive_ui(force=True)
        self.resize(1400, 900)

    def _set_tool_mode(self, mode: str) -> None:
        """Set the current tool mode (pan or select)."""
        if mode not in {"pan", "select"}:
            mode = "select"
        self._current_tool_mode = mode
        if hasattr(self, "_tool_mode_combo"):
            idx = self._tool_mode_combo.findData(mode)
            if idx >= 0 and self._tool_mode_combo.currentIndex() != idx:
                self._tool_mode_combo.blockSignals(True)
                self._tool_mode_combo.setCurrentIndex(idx)
                self._tool_mode_combo.blockSignals(False)
        self._apply_tool_mode_to_viewers()

    def _on_tool_mode_changed(self, _index: int = -1) -> None:
        if not hasattr(self, "_tool_mode_combo"):
            return
        mode = self._tool_mode_combo.currentData()
        self._set_tool_mode(str(mode) if mode else "select")

    def _apply_tool_mode_to_viewers(self) -> None:
        """Apply current tool mode to both PDF viewers."""
        if hasattr(self.left_view, "_image") and hasattr(self.left_view._image, "set_mode"):
            self.left_view._image.set_mode(self._current_tool_mode)  # type: ignore[union-attr]
        if hasattr(self.right_view, "_image") and hasattr(self.right_view._image, "set_mode"):
            self.right_view._image.set_mode(self._current_tool_mode)  # type: ignore[union-attr]

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_responsive_ui()

    def _apply_clean_ui_texts(self) -> None:
        """Override mojibake labels/tooltips with stable UTF-8 UI text."""
        self.setWindowTitle("Verbatim - PDF文档比对工具 v1.0")
        self._tool_mode_combo.setItemText(0, "框选")
        self._tool_mode_combo.setItemText(1, "平移")
        self._tool_mode_combo.setToolTip("当前操作模式（框选/平移）")
        self._btn_left_prev.setText("上一页")
        self._btn_left_next.setText("下一页")
        self._btn_right_prev.setText("上一页")
        self._btn_right_next.setText("下一页")
        self._btn_filter_format.setText("显示格式差异")
        self._btn_filter_format.setToolTip("显示/隐藏格式差异（字体、字号、颜色）")
        self._btn_pure_content.setText("忽略空白")
        self._btn_ignore_punctuation.setText("忽略标点")
        self._btn_normalize_numbers.setText("数字标准")
        self._btn_merge_keyvalue.setText("键值合并")
        self._btn_use_ocr.setText("自动OCR回退")
        self._btn_use_ocr.setToolTip("检测到疑似乱码/低质量文本层时，自动调用OCR回退")
        self._btn_dual_ocr.setText("高噪声双侧OCR")
        self._btn_dual_ocr.setToolTip("当任一侧触发OCR时，强制两侧都使用OCR（降低体系差异噪声）")
        self._btn_ocr_settings.setText("OCR设置")
        self._btn_ocr_settings.setToolTip("设置OCR Token与接口地址")
        self._btn_advanced_settings.setText("高级设置")
        self._btn_advanced_settings.setToolTip("打开高级比对选项")
        self._btn_prealign.setText("预对齐建议")
        self._btn_prealign.setToolTip("查看机器预对齐候选并应用到选区")
        self._btn_compare.setText("开始对比")
        self._btn_save_selection.setText("保存选区")
        self._btn_delete_region.setText("删除选区")
        self._set_tool_mode(self._current_tool_mode)
        self._update_page_meta_labels()
        self._refresh_ocr_ui_state()

    def _update_page_meta_labels(self) -> None:
        if self._compact_ui:
            self._left_page_label.setText(f"{self._left_page_count}页")
            self._right_page_label.setText(f"{self._right_page_count}页")
        else:
            self._left_page_label.setText(f"共 {self._left_page_count} 页")
            self._right_page_label.setText(f"共 {self._right_page_count} 页")

    def _apply_responsive_ui(self, *, force: bool = False) -> None:
        compact = self.width() < 1460
        very_compact = self.width() < 1320
        if not force and compact == self._compact_ui:
            # still update very-compact-only controls when width changes in compact range
            self._btn_save_selection.setVisible(not very_compact)
            return
        self._compact_ui = compact

        if compact:
            self._tool_mode_label.setText("模式")
            self._btn_left_prev.setText("<")
            self._btn_left_next.setText(">")
            self._btn_right_prev.setText("<")
            self._btn_right_next.setText(">")
            self._btn_left_zoom_reset.setText("1:1")
            self._btn_right_zoom_reset.setText("1:1")
            self._btn_ocr_settings.setText("OCR")
            self._btn_advanced_settings.setText("高级")
            self._btn_prealign.setText("预对齐")
            self._btn_compare.setText("对比")
            self._btn_save_selection.setVisible(not very_compact)
            self._left_page_combo.setMinimumWidth(64)
            self._right_page_combo.setMinimumWidth(64)
            self._left_zoom_label.setMinimumWidth(40)
            self._right_zoom_label.setMinimumWidth(40)
        else:
            self._tool_mode_label.setText("工具模式")
            self._btn_left_prev.setText("上一页")
            self._btn_left_next.setText("下一页")
            self._btn_right_prev.setText("上一页")
            self._btn_right_next.setText("下一页")
            self._btn_left_zoom_reset.setText("100%")
            self._btn_right_zoom_reset.setText("100%")
            self._btn_ocr_settings.setText("OCR设置")
            self._btn_advanced_settings.setText("高级设置")
            self._btn_prealign.setText("预对齐建议")
            self._btn_compare.setText("开始对比")
            self._btn_save_selection.setVisible(True)
            self._left_page_combo.setMinimumWidth(78)
            self._right_page_combo.setMinimumWidth(78)
            self._left_zoom_label.setMinimumWidth(48)
            self._right_zoom_label.setMinimumWidth(48)

        self._update_page_meta_labels()

    def _refresh_ocr_ui_state(self) -> None:
        route = self._ocr_route_mode()
        cloud_ok = bool(self._ocr_cfg and self._ocr_cfg.token.strip())
        local_runtime = self._ocr_runtime_dir()
        local_json = self._ocr_json_exe_path()
        local_check = self._get_local_ocr_self_check()
        local_capable = route in {"local_first", "local_only"} and bool(local_check and local_check.available)
        has_any = cloud_ok or local_capable
        storage_mode = (self._ocr_cfg.token_storage if self._ocr_cfg else "none").strip().lower()
        storage_labels = {
            "env": "环境变量",
            "dpapi": "DPAPI 加密",
            "plain": "明文存储",
            "none": "未配置",
        }
        storage_label = storage_labels.get(storage_mode, storage_mode or "未知")
        self._ocr_mode_combo.setEnabled(cloud_ok)
        self._btn_dual_ocr.setEnabled(has_any)
        if has_any:
            src = "环境变量" if (self._ocr_cfg and self._ocr_cfg.source == "env") else "本地设置"
            self._btn_use_ocr.setEnabled(True)
            self._auto_ocr_enabled = True
            if cloud_ok:
                self._btn_use_ocr.setToolTip(f"自动OCR回退已启用（当前配置来源：{src}，存储={storage_label}，路由={route}）")
                self._ocr_token_status.setText(f"OCR Token: ● 已配置（{src}，{storage_label}）")
                local_note = ""
                if route in {"local_first", "local_only"} and local_check is not None and not local_check.available:
                    local_note = f" | {self._format_local_ocr_hint(local_check, local_runtime, local_json)}"
                self._btn_use_ocr.setToolTip(
                    f"自动 OCR 已启用（来源：{src}，存储：{storage_label}，路由：{route}{local_note}）"
                )
                self._ocr_token_status.setText(f"OCR Token: 已配置（{src}/{storage_label}）{local_note}")
            else:
                local_hint = self._format_local_ocr_hint(local_check, local_runtime, local_json)
                self._btn_use_ocr.setToolTip(f"自动 OCR 已启用（本地优先，路由：{route}，{local_hint}）")
                self._ocr_token_status.setText(f"OCR Token: 未配置（本地 OCR：{local_hint}）")
            if self._local_ocr_breaker_open():
                remain = max(0, int(self._local_ocr_cooldown_until - time.monotonic()))
                self._btn_use_ocr.setToolTip(self._btn_use_ocr.toolTip() + f" | 本地OCR熔断中，剩余{remain}s")
            self._ocr_token_status.setStyleSheet("color:#1e8449;font-size:12px;font-weight:600;")
        else:
            self._btn_use_ocr.setChecked(False)
            self._btn_use_ocr.setEnabled(False)
            self._btn_dual_ocr.setChecked(False)
            self._auto_ocr_enabled = False
            blocked_note = ""
            if local_check is not None and not local_check.available:
                blocked_note = f"（{self._format_local_ocr_hint(local_check, local_runtime, local_json)}）"
            self._btn_use_ocr.setToolTip(f"OCR 功能已禁用：无云端配置且无可用本地 OCR{blocked_note}")
            self._ocr_token_status.setText(f"OCR Token: 未配置（未检测到可用本地 OCR）{blocked_note}")
            self._ocr_token_status.setStyleSheet("color:#c0392b;font-size:12px;font-weight:600;")

    @staticmethod
    def _format_local_ocr_hint(
        local_check: LocalOcrSelfCheck | None,
        local_runtime: Path | None,
        local_json: Path | None,
    ) -> str:
        if local_check is None:
            if local_runtime is not None:
                return f"runtime={local_runtime}"
            if local_json is not None:
                return "PaddleOCR-json"
            return "未配置"
        if local_check.available:
            parts: list[str] = []
            if local_check.python_worker_ready:
                parts.append("python-worker-ready")
            if local_check.json_ready:
                parts.append("json-ready")
            return ", ".join(parts) or "local-ready"
        if local_check.code == "numpy_abi_mismatch":
            return "blocked:numpy_abi_mismatch，需独立 OCR 环境（numpy<2）"
        if local_check.code == "module_missing":
            return "blocked:module_missing，需安装 paddlepaddle/paddleocr/paddlex"
        if local_check.code == "worker_missing":
            return "blocked:worker_missing，请设置 VERBATIM_OCR_WORKER_PYTHON"
        return f"blocked:{local_check.code}"

    @staticmethod
    def _build_ocr_storage_note(token_storage: str) -> str:
        storage_mode = str(token_storage or "").strip().lower()
        if storage_mode == "dpapi":
            return "当前 Token 已使用 DPAPI 加密保存。"
        if storage_mode == "plain":
            return "警告：当前 Token 因 DPAPI 不可用而以明文写入本地配置文件。"
        if storage_mode == "env":
            return "当前运行时优先使用环境变量中的 Token；本地保存仅作为后备配置。"
        return "当前 Token 存储状态未知，请复核本地配置。"

    def _toggle_format_diffs(self) -> None:
        """Toggle format diff visibility."""
        # show_format_diffs is True when button is checked (showing format diffs)
        self._show_format_diffs = self._btn_filter_format.isChecked()
        if self._show_format_diffs and self._btn_pure_content.isChecked():
            # Format diff requires non-pure-content mode.
            self._btn_pure_content.setChecked(False)
            self._set_result_summary("已自动关闭“忽略空白”，以启用格式差异显示")

        # Update button text
        if self._btn_filter_format.isChecked():
            self._btn_filter_format.setText("隐藏格式差异")
        else:
            self._btn_filter_format.setText("显示格式差异")

        # Apply to viewers
        if hasattr(self.left_view, "_image"):
            self.left_view._image.set_show_format_diffs(self._show_format_diffs)
        if hasattr(self.right_view, "_image"):
            self.right_view._image.set_show_format_diffs(self._show_format_diffs)

    def _on_pure_content_toggled(self) -> None:
        """Keep pure-content and format-diff modes consistent."""
        if self._btn_pure_content.isChecked() and self._btn_filter_format.isChecked():
            self._btn_filter_format.setChecked(False)
            self._show_format_diffs = False
            self._btn_filter_format.setText("显示格式差异")
            if hasattr(self.left_view, "_image"):
                self.left_view._image.set_show_format_diffs(False)
            if hasattr(self.right_view, "_image"):
                self.right_view._image.set_show_format_diffs(False)
            self._set_result_summary("当前启用“忽略空白”，格式差异已自动关闭", warn=True)

    def _render_zoom_for(self, side: str) -> float:
        ratio = self._left_zoom_ratio if side == "left" else self._right_zoom_ratio
        return float(self._base_render_zoom) * float(ratio)

    def _set_zoom_ratio(self, side: str, ratio: float) -> None:
        ratio = max(ZOOM_MIN, min(ZOOM_MAX, float(ratio)))
        if side == "left":
            if abs(ratio - self._left_zoom_ratio) < 1e-9:
                return
            self._left_zoom_ratio = ratio
            self._reload_side_view("left")
        else:
            if abs(ratio - self._right_zoom_ratio) < 1e-9:
                return
            self._right_zoom_ratio = ratio
            self._reload_side_view("right")
        self._refresh_zoom_ui()

    def _adjust_zoom(self, side: str, delta: float) -> None:
        cur = self._left_zoom_ratio if side == "left" else self._right_zoom_ratio
        self._set_zoom_ratio(side, cur + float(delta))

    def _refresh_zoom_ui(self) -> None:
        left_pct = int(round(self._left_zoom_ratio * 100))
        right_pct = int(round(self._right_zoom_ratio * 100))
        self._left_zoom_label.setText(f"{left_pct}%")
        self._right_zoom_label.setText(f"{right_pct}%")
        self._btn_left_zoom_out.setEnabled(self._left_zoom_ratio > ZOOM_MIN + 1e-9)
        self._btn_left_zoom_in.setEnabled(self._left_zoom_ratio < ZOOM_MAX - 1e-9)
        self._btn_right_zoom_out.setEnabled(self._right_zoom_ratio > ZOOM_MIN + 1e-9)
        self._btn_right_zoom_in.setEnabled(self._right_zoom_ratio < ZOOM_MAX - 1e-9)

    def _reload_side_view(self, side: str) -> None:
        if side == "left":
            if self._left_pdf is None:
                return
            self._load_into_view(self.left_view, self._left_pdf, page_number=self._left_page_number, side="left")
            if self._rerender_diff_overlays(keep_focus=True):
                return
            self.left_view.set_overlays([])
            if self._left_sel_bbox:
                z = self._render_zoom_for("left")
                rect = QRectF(
                    self._left_sel_bbox[0] * z,
                    self._left_sel_bbox[1] * z,
                    (self._left_sel_bbox[2] - self._left_sel_bbox[0]) * z,
                    (self._left_sel_bbox[3] - self._left_sel_bbox[1]) * z,
                )
                self.left_view.set_selected_overlays([(rect, QColor(52, 152, 219, 180))])
        else:
            if self._right_pdf is None:
                return
            self._load_into_view(self.right_view, self._right_pdf, page_number=self._right_page_number, side="right")
            if self._rerender_diff_overlays(keep_focus=True):
                return
            self.right_view.set_overlays([])
            if self._right_sel_bbox:
                z = self._render_zoom_for("right")
                rect = QRectF(
                    self._right_sel_bbox[0] * z,
                    self._right_sel_bbox[1] * z,
                    (self._right_sel_bbox[2] - self._right_sel_bbox[0]) * z,
                    (self._right_sel_bbox[3] - self._right_sel_bbox[1]) * z,
                )
                self.right_view.set_selected_overlays([(rect, QColor(46, 204, 113, 180))])

    def _load_into_view(
        self, view: PdfImageViewer, pdf_path: Path, page_number: int = 0, *, side: str = "left"
    ) -> None:
        if not pdf_path.exists():
            view.set_error(f"Missing file: {pdf_path}")
            return

        try:
            view.set_pixmap(self._render_page_pixmap(pdf_path, page_number=page_number, side=side))
        except Exception as e:
            view.set_error(f"Failed to render {pdf_path.name}: {e}")

    def _render_page_pixmap(self, pdf_path: Path, *, page_number: int = 0, side: str = "left") -> QPixmap:
        png_bytes = self._run_process_task_with_ui_pump(
            "render_page_png",
            str(pdf_path),
            int(page_number),
            float(self._render_zoom_for(side)),
        )
        pix = QPixmap()
        # PySide6 on Windows rejects the explicit format overload here even for valid PNG bytes.
        # Let Qt sniff the image header instead so rendered pages decode reliably.
        if not pix.loadFromData(cast(bytes, png_bytes)):
            raise RuntimeError("failed to decode rendered PNG bytes")
        return pix

    def _load_page_data(self) -> None:
        """Parse both PDFs into PageData (cached for region extraction)."""

        self._left_page = None
        self._right_page = None

        if self._left_pdf is None or self._right_pdf is None:
            return

        try:
            self._left_page = self._run_process_task_with_ui_pump(
                "parse_page",
                str(self._left_pdf),
                int(self._left_page_number),
            )
            self._right_page = self._run_process_task_with_ui_pump(
                "parse_page",
                str(self._right_pdf),
                int(self._right_page_number),
            )

            self._apply_page_text_layer_status("left", self._left_page_number, self._left_page, verbose=False)
            self._apply_page_text_layer_status("right", self._right_page_number, self._right_page, verbose=False)

        except Exception as e:
            print(f"[verbatim] Failed to parse PDFs: {e}")

    def _apply_page_text_layer_status(self, side: str, page_number: int, page: PageData | None, *, verbose: bool) -> None:
        if page is None:
            return
        side_label = "Left" if side == "left" else "Right"
        status = evaluate_page_text_layer(
            side_label=side_label,
            page_number=int(page_number),
            text_char_count=len(page.text_chars),
        )
        if status.brief_log:
            print(status.brief_log)
        if verbose:
            for line in status.warning_banner:
                print(line)
        if not status.force_ocr:
            return
        if side == "left":
            self._left_force_ocr = True
            return
        self._right_force_ocr = True

    def _choose_left_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Left PDF",
            "",
            "PDF Files (*.pdf)",
        )
        if not path:
            return
        self._set_left_pdf(Path(path))

    def _choose_right_pdf(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Right PDF",
            "",
            "PDF Files (*.pdf)",
        )
        if not path:
            return
        self._set_right_pdf(Path(path))

    def _set_left_pdf(self, pdf_path: Path) -> None:
        self._clear_cached_diff_result()
        self._ocr_result_cache.clear()
        self._ocr_result_spans_cache.clear()
        self._ocr_result_spans_meta.clear()
        self._left_pdf = pdf_path
        self._left_doc_profile = None
        self._left_zoom_ratio = 1.0
        self._refresh_zoom_ui()

        # Get page count
        try:
            doc = fitz.open(pdf_path)
            self._left_page_count = doc.page_count
            doc.close()
        except Exception as e:
            print(f"[verbatim] Failed to get page count: {e}")
            self._left_page_count = 1

        # Update navigation controls
        self._left_page_combo.setEnabled(True)
        self._left_page_combo.blockSignals(True)
        self._left_page_combo.clear()
        for i in range(self._left_page_count):
            self._left_page_combo.addItem(f"第{i + 1}页")
        self._left_page_combo.setCurrentIndex(0)
        self._left_page_combo.blockSignals(False)
        self._update_page_meta_labels()
        self._btn_left_prev.setEnabled(False)
        self._btn_left_next.setEnabled(self._left_page_count > 1)

        # Reset page number
        self._left_page_number = 0
        self._left_committed_page_number = 0
        self._left_nav_epoch = 0
        self._left_nav_loading = False
        self._left_force_ocr, self._left_doc_quality_note = self._assess_pdf_side_quality(
            pdf_path, self._left_page_count, "左侧"
        )
        print(f"[verbatim] {self._left_doc_quality_note}")

        self.left_view.set_title(f"左侧: {pdf_path.name} (第1页)")
        self.left_view.set_overlays([])
        self.left_view.set_selected_overlays([])
        self._load_into_view(self.left_view, pdf_path, page_number=0, side="left")
        self._maybe_parse_pages()

    def _set_right_pdf(self, pdf_path: Path) -> None:
        self._clear_cached_diff_result()
        self._ocr_result_cache.clear()
        self._ocr_result_spans_cache.clear()
        self._ocr_result_spans_meta.clear()
        self._right_pdf = pdf_path
        self._right_doc_profile = None
        self._right_zoom_ratio = 1.0
        self._refresh_zoom_ui()

        # Get page count
        try:
            doc = fitz.open(pdf_path)
            self._right_page_count = doc.page_count
            doc.close()
        except Exception as e:
            print(f"[verbatim] Failed to get page count: {e}")
            self._right_page_count = 1

        # Update navigation controls
        self._right_page_combo.setEnabled(True)
        self._right_page_combo.blockSignals(True)
        self._right_page_combo.clear()
        for i in range(self._right_page_count):
            self._right_page_combo.addItem(f"第{i + 1}页")
        self._right_page_combo.setCurrentIndex(0)
        self._right_page_combo.blockSignals(False)
        self._update_page_meta_labels()
        self._btn_right_prev.setEnabled(False)
        self._btn_right_next.setEnabled(self._right_page_count > 1)

        # Reset page number
        self._right_page_number = 0
        self._right_committed_page_number = 0
        self._right_nav_epoch = 0
        self._right_nav_loading = False
        self._right_force_ocr, self._right_doc_quality_note = self._assess_pdf_side_quality(
            pdf_path, self._right_page_count, "右侧"
        )
        print(f"[verbatim] {self._right_doc_quality_note}")

        self.right_view.set_title(f"右侧: {pdf_path.name} (第1页)")
        self.right_view.set_overlays([])
        self.right_view.set_selected_overlays([])
        self._load_into_view(self.right_view, pdf_path, page_number=0, side="right")
        self._maybe_parse_pages()

    def _left_prev_page(self) -> None:
        if self._left_page_number > 0:
            self._set_page_index("left", self._left_page_number - 1)

    def _left_next_page(self) -> None:
        if self._left_page_number < self._left_page_count - 1:
            self._set_page_index("left", self._left_page_number + 1)

    def _left_page_combo_changed(self, index: int) -> None:
        self._set_page_index("left", index)

    def _update_left_page(self) -> None:
        if self._left_pdf is None:
            return
        self._left_nav_loading = True
        self._sync_page_controls("left")
        while True:
            requested_page = self._left_page_number
            requested_epoch = self._left_nav_epoch
            self._clear_cached_diff_result()
            self.left_view.set_overlays([])
            self.left_view.set_selected_overlays([])
            self._left_sel_bbox = None
            self._update_compare_enabled()
            try:
                pixmap = self._render_page_pixmap(self._left_pdf, page_number=requested_page, side="left")
                page = self._run_process_task_with_ui_pump(
                    "parse_page",
                    str(self._left_pdf),
                    int(requested_page),
                )
            except Exception as e:
                if requested_epoch != self._left_nav_epoch or requested_page != self._left_page_number:
                    continue
                self.left_view.set_error(f"Failed to render {self._left_pdf.name}: {e}")
                print(f"[verbatim] Failed to parse left page: {e}")
                break
            if requested_epoch != self._left_nav_epoch or requested_page != self._left_page_number:
                continue
            self.left_view.set_pixmap(pixmap)
            self._left_page = page
            self._left_committed_page_number = requested_page
            self._apply_page_text_layer_status("left", requested_page, self._left_page, verbose=True)
            break
        self._left_nav_loading = False
        self._sync_page_controls("left")

    def _clamp_page_index(self, side: str, index: int) -> int:
        page_count = self._left_page_count if side == "left" else self._right_page_count
        if page_count <= 0:
            return 0
        return max(0, min(int(index), page_count - 1))

    def _sync_page_controls(self, side: str) -> None:
        if side == "left":
            combo = self._left_page_combo
            page_number = self._left_page_number
            committed_page = self._left_committed_page_number
            page_count = self._left_page_count
            pdf = self._left_pdf
            is_loading = self._left_nav_loading
            self._btn_left_prev.setEnabled((not is_loading) and page_number > 0)
            self._btn_left_next.setEnabled((not is_loading) and page_number < page_count - 1)
            combo.setEnabled((not is_loading) and page_count > 0)
            if pdf is not None:
                suffix = " (加载中)" if is_loading and page_number != committed_page else ""
                self.left_view.set_title(f"左侧: {pdf.name} (第{page_number + 1}页){suffix}")
        else:
            combo = self._right_page_combo
            page_number = self._right_page_number
            committed_page = self._right_committed_page_number
            page_count = self._right_page_count
            pdf = self._right_pdf
            is_loading = self._right_nav_loading
            self._btn_right_prev.setEnabled((not is_loading) and page_number > 0)
            self._btn_right_next.setEnabled((not is_loading) and page_number < page_count - 1)
            combo.setEnabled((not is_loading) and page_count > 0)
            if pdf is not None:
                suffix = " (加载中)" if is_loading and page_number != committed_page else ""
                self.right_view.set_title(f"右侧: {pdf.name} (第{page_number + 1}页){suffix}")

        clamped_index = self._clamp_page_index(side, page_number)
        if combo.currentIndex() != clamped_index:
            combo.blockSignals(True)
            combo.setCurrentIndex(clamped_index)
            combo.blockSignals(False)

    def _set_page_index(self, side: str, index: int) -> None:
        clamped_index = self._clamp_page_index(side, index)
        if side == "left":
            self._left_page_number = clamped_index
            self._left_nav_epoch += 1
            self._sync_page_controls("left")
            if not self._left_nav_loading:
                self._update_left_page()
            return
        self._right_page_number = clamped_index
        self._right_nav_epoch += 1
        self._sync_page_controls("right")
        if not self._right_nav_loading:
            self._update_right_page()

    def _right_prev_page(self) -> None:
        if self._right_page_number > 0:
            self._set_page_index("right", self._right_page_number - 1)

    def _right_next_page(self) -> None:
        if self._right_page_number < self._right_page_count - 1:
            self._set_page_index("right", self._right_page_number + 1)

    def _right_page_combo_changed(self, index: int) -> None:
        self._set_page_index("right", index)

    def _update_right_page(self) -> None:
        if self._right_pdf is None:
            return
        self._right_nav_loading = True
        self._sync_page_controls("right")
        while True:
            requested_page = self._right_page_number
            requested_epoch = self._right_nav_epoch
            self._clear_cached_diff_result()
            self.right_view.set_overlays([])
            self.right_view.set_selected_overlays([])
            self._right_sel_bbox = None
            self._update_compare_enabled()
            try:
                pixmap = self._render_page_pixmap(self._right_pdf, page_number=requested_page, side="right")
                page = self._run_process_task_with_ui_pump(
                    "parse_page",
                    str(self._right_pdf),
                    int(requested_page),
                )
            except Exception as e:
                if requested_epoch != self._right_nav_epoch or requested_page != self._right_page_number:
                    continue
                self.right_view.set_error(f"Failed to render {self._right_pdf.name}: {e}")
                print(f"[verbatim] Failed to parse right page: {e}")
                break
            if requested_epoch != self._right_nav_epoch or requested_page != self._right_page_number:
                continue
            self.right_view.set_pixmap(pixmap)
            self._right_page = page
            self._right_committed_page_number = requested_page
            self._apply_page_text_layer_status("right", requested_page, self._right_page, verbose=True)
            break
        self._right_nav_loading = False
        self._sync_page_controls("right")

    def _maybe_parse_pages(self) -> None:
        """Parse pages once both PDFs are selected."""
        self._clear_cached_diff_result()

        # Reset selection state
        self._prealign_active = False
        self._prealign_manual_adjust_steps = 0
        self._prealign_base_left_bbox = None
        self._prealign_base_right_bbox = None
        self._prealign_last_left_bbox = None
        self._prealign_last_right_bbox = None
        self._left_sel_bbox = None
        self._right_sel_bbox = None
        self._btn_compare.setEnabled(False)
        self.left_view.set_selected_overlays([])
        self.right_view.set_selected_overlays([])
        self._populate_diff_list([])

        if self._left_pdf is None or self._right_pdf is None:
            return

        self._load_page_data()

        # Update region history when pages change
        self._update_region_history()

    def _qrect_to_pdf_bbox(self, rect: QRect, *, page_w: float, page_h: float, render_zoom: float) -> BBox:
        r = rect.normalized()
        x0 = r.left() / render_zoom
        y0 = r.top() / render_zoom
        x1 = (r.left() + r.width()) / render_zoom
        y1 = (r.top() + r.height()) / render_zoom

        # Clamp to page.
        x0 = max(0.0, min(float(page_w), float(x0)))
        y0 = max(0.0, min(float(page_h), float(y0)))
        x1 = max(0.0, min(float(page_w), float(x1)))
        y1 = max(0.0, min(float(page_h), float(y1)))
        return (float(x0), float(y0), float(x1), float(y1))

    def _on_left_selection_finished(self, rect: QRect) -> None:
        """Record left selection bbox (PDF coords). Compare is run explicitly."""
        if self._is_comparing:
            return

        if self._left_page is None:
            print("[verbatim] Left page data not ready yet; please select PDFs first.")
            return

        self._left_sel_bbox = self._qrect_to_pdf_bbox(
            rect,
            page_w=self._left_page.width,
            page_h=self._left_page.height,
            render_zoom=self._render_zoom_for("left"),
        )

        # Show selection box with blue color
        sel_rect = QRectF(rect)
        self.left_view.set_selected_overlays([(sel_rect, QColor(52, 152, 219, 180))])  # Blue selection

        # Clear sync selection overlay on right side
        self.right_view._image.set_sync_selection(None)  # type: ignore[attr-defined]

        # Extract and preview text (with strict bounds to prevent overflow)
        temp_region = extract_region(
            self._left_page,
            [self._left_sel_bbox],
            strict_bounds=True,
            reading_order_mode=self._current_reading_order_mode(),
        )
        text = "".join(ch.char for ch in temp_region.chars)
        char_count = len(temp_region.chars)

        print(f"[verbatim] Left region selected: {char_count} chars")
        self._log_text_preview("Extracted text", text)
        self._track_manual_adjust("left", self._left_sel_bbox)

        self._update_compare_enabled()

    def _on_left_selection_changing(self, rect: QRect) -> None:
        """Real-time feedback when user is selecting on left side.

        Shows a shadow mask on the RIGHT viewer to indicate "system will only
        look in this area for comparison".

        IMPORTANT: Must convert coordinates considering different page sizes!
        """
        if self._is_comparing:
            return
        if not hasattr(self.right_view._image, "set_sync_selection"):
            return

        if self._left_page is None or self._right_page is None:
            return

        # Step 1: Convert left screen coords to left PDF coords
        left_zoom = self._render_zoom_for("left")
        right_zoom = self._render_zoom_for("right")
        left_pdf_x0 = rect.left() / left_zoom
        left_pdf_y0 = rect.top() / left_zoom
        left_pdf_x1 = rect.right() / left_zoom
        left_pdf_y1 = rect.bottom() / left_zoom

        # Step 2: Scale to right PDF coords (proportional mapping)
        # This handles different page sizes between left and right PDFs
        left_w = self._left_page.width
        left_h = self._left_page.height
        right_w = self._right_page.width
        right_h = self._right_page.height

        right_pdf_x0 = left_pdf_x0 * (right_w / left_w)
        right_pdf_y0 = left_pdf_y0 * (right_h / left_h)
        right_pdf_x1 = left_pdf_x1 * (right_w / left_w)
        right_pdf_y1 = left_pdf_y1 * (right_h / left_h)

        # Step 3: Convert right PDF coords to right screen coords
        sync_rect = QRectF(
            right_pdf_x0 * right_zoom,
            right_pdf_y0 * right_zoom,
            (right_pdf_x1 - right_pdf_x0) * right_zoom,
            (right_pdf_y1 - right_pdf_y0) * right_zoom,
        )

        self.right_view._image.set_sync_selection(sync_rect)  # type: ignore[attr-defined]

    def _on_right_selection_finished(self, rect: QRect) -> None:
        """Record right selection bbox (PDF coords). Compare is run explicitly."""
        if self._is_comparing:
            return

        if self._right_page is None:
            print("[verbatim] Right page data not ready yet; please select PDFs first.")
            return

        self._right_sel_bbox = self._qrect_to_pdf_bbox(
            rect,
            page_w=self._right_page.width,
            page_h=self._right_page.height,
            render_zoom=self._render_zoom_for("right"),
        )

        # Show selection box with green color
        sel_rect = QRectF(rect)
        self.right_view.set_selected_overlays([(sel_rect, QColor(46, 204, 113, 180))])  # Green selection

        # Clear sync selection overlay on left side
        self.left_view._image.set_sync_selection(None)  # type: ignore[attr-defined]

        # Extract and preview text (with strict bounds to prevent overflow)
        temp_region = extract_region(
            self._right_page,
            [self._right_sel_bbox],
            strict_bounds=True,
            reading_order_mode=self._current_reading_order_mode(),
        )
        text = "".join(ch.char for ch in temp_region.chars)
        char_count = len(temp_region.chars)

        print(f"[verbatim] Right region selected: {char_count} chars")
        self._log_text_preview("Extracted text", text)
        self._track_manual_adjust("right", self._right_sel_bbox)

        self._update_compare_enabled()

    def _on_right_selection_changing(self, rect: QRect) -> None:
        """Real-time feedback when user is selecting on right side.

        Shows a shadow mask on the LEFT viewer to indicate "system will only
        look in this area for comparison".

        IMPORTANT: Must convert coordinates considering different page sizes!
        """
        if self._is_comparing:
            return
        if not hasattr(self.left_view._image, "set_sync_selection"):
            return

        if self._left_page is None or self._right_page is None:
            return

        # Step 1: Convert right screen coords to right PDF coords
        right_zoom = self._render_zoom_for("right")
        left_zoom = self._render_zoom_for("left")
        right_pdf_x0 = rect.left() / right_zoom
        right_pdf_y0 = rect.top() / right_zoom
        right_pdf_x1 = rect.right() / right_zoom
        right_pdf_y1 = rect.bottom() / right_zoom

        # Step 2: Scale to left PDF coords (proportional mapping)
        # This handles different page sizes between left and right PDFs
        left_w = self._left_page.width
        left_h = self._left_page.height
        right_w = self._right_page.width
        right_h = self._right_page.height

        left_pdf_x0 = right_pdf_x0 * (left_w / right_w)
        left_pdf_y0 = right_pdf_y0 * (left_h / right_h)
        left_pdf_x1 = right_pdf_x1 * (left_w / right_w)
        left_pdf_y1 = right_pdf_y1 * (left_h / right_h)

        # Step 3: Convert left PDF coords to left screen coords
        sync_rect = QRectF(
            left_pdf_x0 * left_zoom,
            left_pdf_y0 * left_zoom,
            (left_pdf_x1 - left_pdf_x0) * left_zoom,
            (left_pdf_y1 - left_pdf_y0) * left_zoom,
        )

        self.left_view._image.set_sync_selection(sync_rect)  # type: ignore[attr-defined]

    def _update_compare_enabled(self) -> None:
        ready = (
            not self._left_nav_loading
            and not self._right_nav_loading
            and
            self._left_page is not None
            and self._right_page is not None
            and self._left_sel_bbox is not None
            and self._right_sel_bbox is not None
        )
        self._btn_compare.setEnabled(bool(ready))

        state = (
            bool(ready),
            self._left_page is not None,
            self._right_page is not None,
            self._left_sel_bbox is not None,
            self._right_sel_bbox is not None,
        )
        if self._last_compare_debug_state == state:
            return
        self._last_compare_debug_state = state
        print(f"[verbatim] Compare button state: {'ENABLED' if ready else 'DISABLED'}")
        print(f"  - left_page: {'OK' if self._left_page else 'None'}")
        print(f"  - right_page: {'OK' if self._right_page else 'None'}")
        print(f"  - left_sel_bbox: {'OK' if self._left_sel_bbox else 'None'}")
        print(f"  - right_sel_bbox: {'OK' if self._right_sel_bbox else 'None'}")

    def _begin_compare_feedback(self) -> None:
        self._is_comparing = True
        self._trace_id = uuid.uuid4().hex
        log_event("CMP_START", "compare started", level="info", trace_id=self._trace_id)
        self.statusBar().showMessage("正在比对，请等待...")
        self._lock_compare_inputs()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()

    def _end_compare_feedback(self) -> None:
        self._is_comparing = False
        log_event("CMP_END", "compare ended", level="info", trace_id=self._trace_id)
        self.statusBar().clearMessage()
        self._unlock_compare_inputs()
        if QApplication.overrideCursor() is not None:
            QApplication.restoreOverrideCursor()
        QApplication.processEvents()

    def _lock_compare_inputs(self) -> None:
        if getattr(self, "_compare_input_locked", False):
            return
        self._compare_input_locked = True
        self._compare_input_state = {}

        def _lock(attr: str) -> None:
            if hasattr(self, attr):
                widget = getattr(self, attr)
                if hasattr(widget, "isEnabled") and hasattr(widget, "setEnabled"):
                    self._compare_input_state[attr] = widget.isEnabled()
                    widget.setEnabled(False)

        for name in (
            "_btn_compare",
            "_btn_save_selection",
            "_compare_history_list",
            "_region_list",
            "_btn_delete_region",
            "_left_page_combo",
            "_right_page_combo",
            "_btn_left_prev",
            "_btn_left_next",
            "_btn_right_prev",
            "_btn_right_next",
            "_btn_use_ocr",
            "_btn_dual_ocr",
            "_btn_ocr_settings",
            "_btn_ocr_diag",
            "_btn_pure_content",
            "_btn_filter_format",
            "_btn_ignore_punctuation",
            "_btn_normalize_numbers",
            "_btn_merge_keyvalue",
            "_reading_order_combo",
            "_ocr_mode_combo",
        ):
            _lock(name)

    def _unlock_compare_inputs(self) -> None:
        if not getattr(self, "_compare_input_locked", False):
            return
        for attr, enabled in (self._compare_input_state or {}).items():
            if hasattr(self, attr):
                widget = getattr(self, attr)
                if hasattr(widget, "setEnabled"):
                    widget.setEnabled(bool(enabled))
        self._compare_input_state = {}
        self._compare_input_locked = False

    def _set_result_summary(self, text: str, *, warn: bool = False) -> None:
        self._last_compare_summary = text
        if hasattr(self, "_result_summary_bar"):
            self._result_summary_bar.setText(f"结果摘要：{text}")
            if warn:
                self._result_summary_bar.setStyleSheet(
                    "background:#fff6e5;color:#8a5a00;border-bottom:1px solid #e6d2a3;padding:6px 12px;font-size:12px;"
                )
            else:
                self._result_summary_bar.setStyleSheet(
                    "background:#f7f9fb;color:#34495e;border-bottom:1px solid #dde3e8;padding:6px 12px;font-size:12px;"
                )

    @staticmethod
    def _reliability_level(left_quality: dict, right_quality: dict, *, ocr_used: bool, ocr_errors: list[str]) -> str:
        lq = str((left_quality or {}).get("quality", "good"))
        rq = str((right_quality or {}).get("quality", "good"))
        if lq == "bad" or rq == "bad":
            return "低"
        if ocr_errors:
            return "中"
        if ocr_used and (lq == "warning" or rq == "warning"):
            return "中"
        return "高"

    @staticmethod
    def _op_content_span_len(op) -> int:
        left_seg = str(getattr(op, "meta", {}).get("left_text", "") or "").strip()
        right_seg = str(getattr(op, "meta", {}).get("right_text", "") or "").strip()
        return max(len(left_seg), len(right_seg))

    @staticmethod
    def _has_timeout_error(ocr_errors: list[str], ocr_error_codes: list[str] | None = None) -> bool:
        if ocr_error_codes and any(code == "timeout" for code in ocr_error_codes):
            return True
        txt = " | ".join(ocr_errors or []).lower()
        return ("timed out" in txt) or ("timeout" in txt) or ("exceeded" in txt)

    def _record_ocr_error(
        self,
        side_label: str,
        err: Exception | str,
        *,
        variant: int | None = None,
        code_override: str | None = None,
    ) -> None:
        info = classify_ocr_error(err)
        code = code_override or info.code
        detail = str(info.message or "").strip()
        label = f"{side_label}"
        if variant is not None:
            label = f"{label} variant#{variant}"
        if detail:
            entry = f"{label}: {code} - {detail}"
        else:
            entry = f"{label}: {code}"
        self._last_ocr_errors.append(entry)
        self._last_ocr_error_codes.append(code)
        log_event(
            "OCR_ERROR_CLASSIFIED",
            "ocr error classified",
            level="warning",
            trace_id=self._trace_id,
            side=side_label,
            error_type=code,
            detail=detail,
        )

    @staticmethod
    def _in_test_runtime() -> bool:
        return bool(os.getenv("PYTEST_CURRENT_TEST"))

    def _run_manual_review_gate(
        self,
        *,
        reason: str,
        left_text: str,
        right_text: str,
    ) -> tuple[bool, str, str]:
        if not self._manual_review_gate_required:
            return True, left_text, right_text

        if self._in_test_runtime():
            # Keep GUI tests non-blocking while preserving gate semantics.
            return True, left_text, right_text

        dlg = QDialog(self)
        dlg.setWindowTitle("人工确认（必经）")
        dlg.resize(860, 620)

        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        tip = QLabel(f"当前结果低可信，必须人工确认后才能继续比对。\n触发原因：{reason}\n文本为只读确认，确认后继续可能仍有误差。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#7a4b00;background:#fff7e6;border:1px solid #ffd591;padding:8px;")
        layout.addWidget(tip)

        form = QFormLayout()
        left_edit = QTextEdit()
        left_edit.setPlainText(left_text or "")
        left_edit.setPlaceholderText("左侧文本（只读）")
        left_edit.setReadOnly(True)
        right_edit = QTextEdit()
        right_edit.setPlainText(right_text or "")
        right_edit.setPlaceholderText("右侧文本（只读）")
        right_edit.setReadOnly(True)
        form.addRow("左侧文本", left_edit)
        form.addRow("右侧文本", right_edit)
        layout.addLayout(form)

        btn_box = QDialogButtonBox()
        btn_confirm = btn_box.addButton("确认并继续", QDialogButtonBox.ButtonRole.AcceptRole)
        btn_cancel = btn_box.addButton("取消本次比对", QDialogButtonBox.ButtonRole.RejectRole)
        btn_confirm.clicked.connect(dlg.accept)
        btn_cancel.clicked.connect(dlg.reject)
        layout.addWidget(btn_box)

        if dlg.exec() != int(QDialog.DialogCode.Accepted):
            return False, left_text, right_text
        return True, left_edit.toPlainText(), right_edit.toPlainText()

    def _compare_decision_status(
        self,
        *,
        left_text: str,
        right_text: str,
        left_quality: dict,
        right_quality: dict,
        ocr_used: bool,
        left_ocr_applied: bool,
        right_ocr_applied: bool,
        ocr_errors: list[str],
        ocr_was_recommended: bool = False,
        ocr_state: OcrRunState | None = None,
        ocr_state_reason: str = "",
        ocr_error_codes: list[str] | None = None,
        left_coords_reliable: bool = True,
        right_coords_reliable: bool = True,
    ) -> tuple[str, str]:
        if ocr_was_recommended and ocr_state == OcrRunState.BLOCKED:
            return "REVIEW", f"OCR未实际运行（原因={ocr_state_reason or 'blocked'}），已降级为人工复核"
        if ocr_state == OcrRunState.FAILURE:
            return "REVIEW", f"OCR未返回可信结果（原因={ocr_state_reason or 'failure'}），已降级为人工复核"
        if ocr_was_recommended and not ocr_used:
            return "REVIEW", "OCR被建议但未产出有效结果，已降级为人工复核"
        if not ocr_used:
            return "PASS", ""
        sim = self._normalized_similarity(left_text, right_text)
        lc = int((left_quality or {}).get("confidence", 0) or 0)
        rc = int((right_quality or {}).get("confidence", 0) or 0)
        lq = str((left_quality or {}).get("quality", "good"))
        rq = str((right_quality or {}).get("quality", "good"))
        timeout_hit = self._has_timeout_error(ocr_errors, ocr_error_codes)
        any_ocr = bool(left_ocr_applied or right_ocr_applied)

        # Conservative gate for OCR-heavy flows: unreliable OCR should not produce hard diffs.
        if timeout_hit and any_ocr and (lq != "good" or rq != "good"):
            return "REVIEW", "OCR超时且文本质量不足，已降级为人工复核"
        if any_ocr and sim >= 0.97 and (lq != "good" or rq != "good"):
            return "REVIEW", f"OCR场景文本高相似(sim={sim:.3f})，疑似噪声差异，已降级为人工复核"
        if any_ocr and (not left_coords_reliable or not right_coords_reliable):
            return "REFERENCE_ONLY", "OCR文本可用但定位不可信，已降级为文本参考结果"
        if any_ocr and (lc < 82 or rc < 82 or lq != "good" or rq != "good"):
            return "REFERENCE_ONLY", f"OCR文本已采用但置信度偏低(left={lc}, right={rc})，结果仅供参考"
        return "PASS", ""

    @staticmethod
    def _decision_basis_for_compare(*, ocr_used: bool, visual_diff_used: bool) -> str:
        if visual_diff_used:
            return "raster"
        if ocr_used:
            return "ocr"
        return "text"

    def _build_visual_diff_ops(self) -> list[DiffOp]:
        if (
            self._left_pdf is None
            or self._right_pdf is None
            or self._left_sel_bbox is None
            or self._right_sel_bbox is None
        ):
            return []
        payload = self._run_process_task_with_ui_pump(
            "compute_visual_diff",
            self._left_pdf,
            self._right_pdf,
            self._left_page_number,
            self._right_page_number,
            self._left_sel_bbox,
            self._right_sel_bbox,
            zoom=2.0,
            diff_threshold=24,
            timeout_ms=15000,
        )
        ops: list[DiffOp] = []
        for item in payload or []:
            if not isinstance(item, dict):
                continue
            left_bbox = item.get("left_bbox")
            right_bbox = item.get("right_bbox")
            if not isinstance(left_bbox, (list, tuple)) or len(left_bbox) != 4:
                continue
            if not isinstance(right_bbox, (list, tuple)) or len(right_bbox) != 4:
                continue
            ops.append(
                DiffOp(
                    type=DiffOpType.VISUAL_DIFF,
                    left_indices=[],
                    right_indices=[],
                    left_bboxes=[cast(BBox, tuple(float(v) for v in left_bbox))],
                    right_bboxes=[cast(BBox, tuple(float(v) for v in right_bbox))],
                    meta={
                        "left_text": "",
                        "right_text": "",
                        "source": "raster",
                        "score": float(item.get("score", 0.0) or 0.0),
                        "diff_pixels": int(item.get("diff_pixels", 0) or 0),
                    },
                )
            )
        return ops

    def _should_block_low_reliability_diffs(
        self,
        *,
        ops,
        left_text: str,
        right_text: str,
        left_quality: dict,
        right_quality: dict,
        ocr_used: bool,
        left_ocr_applied: bool,
        right_ocr_applied: bool,
    ) -> tuple[bool, str]:
        if not ocr_used:
            return False, ""
        if not ops:
            return False, ""

        sim = self._normalized_similarity(left_text, right_text)
        lq = str((left_quality or {}).get("quality", "good"))
        rq = str((right_quality or {}).get("quality", "good"))
        lc = int((left_quality or {}).get("confidence", 0) or 0)
        rc = int((right_quality or {}).get("confidence", 0) or 0)
        small_ops = sum(1 for op in ops if self._op_content_span_len(op) <= 3)
        small_ratio = (small_ops / len(ops)) if ops else 0.0
        any_ocr_applied = bool(left_ocr_applied or right_ocr_applied)

        if sim >= 0.985 and (lq != "good" or rq != "good"):
            return True, f"OCR场景文本高度相似(sim={sim:.3f})，低可信微差异已抑制"
        if sim >= 0.97 and any_ocr_applied and (lc < 80 or rc < 80) and small_ratio >= 0.6:
            return True, (
                f"OCR结果置信度不足(left={lc}, right={rc})，微差异占比高({small_ratio:.0%})，已降级为人工复核"
            )
        if self._last_ocr_errors and sim >= 0.95 and small_ratio >= 0.5:
            return True, "OCR链路存在错误且文本高相似，已抑制低可信差异"
        return False, ""

    def _clear_cached_diff_result(self) -> None:
        self._last_diff_ops = []
        self._last_left_region = None
        self._last_right_region = None
        self._last_left_ocr_applied = False
        self._last_right_ocr_applied = False
        self._last_left_spans = []
        self._last_right_spans = []
        self._last_left_spans_meta = None
        self._last_right_spans_meta = None
        self._last_left_coords_reliable = True
        self._last_right_coords_reliable = True
        self._focused_diff_op = None
        # Also clear current visual diff artifacts to avoid cross-page stale badges.
        if hasattr(self, "left_view"):
            self.left_view.set_overlays([])
            self.left_view.set_badges([])
            self.left_view.set_selected_overlays([])
        if hasattr(self, "right_view"):
            self.right_view.set_overlays([])
            self.right_view.set_badges([])
            self.right_view.set_selected_overlays([])

    def _log_text_preview(self, label: str, text: str, limit: int = 80) -> None:
        """Avoid leaking sensitive business text in default logs."""
        t = (text or "").strip()
        if self._debug_log_text:
            preview = repr(t[:limit])
            tail = "..." if len(t) > limit else ""
            print(f"[verbatim] {label}: {preview}{tail}")
            return
        print(f"[verbatim] {label}: len={len(t)} (set VERBATIM_DEBUG_LOG_TEXT=1 to view preview)")

    def _current_reading_order_mode(self) -> str:
        """Return reading-order mode selected by user."""
        if not hasattr(self, "_reading_order_combo"):
            return "auto"
        data = self._reading_order_combo.currentData()
        return str(data) if data else "auto"

    def _current_ocr_mode(self) -> str:
        if not hasattr(self, "_ocr_mode_combo"):
            return "sync"
        data = self._ocr_mode_combo.currentData()
        return str(data) if data else "sync"

    def _open_ocr_settings(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("OCR设置")
        layout = QVBoxLayout(dlg)

        form = QFormLayout()
        existing_cfg = self._ocr_cfg
        existing_file_token = ""
        if existing_cfg and existing_cfg.source == "file":
            existing_file_token = (existing_cfg.token or "").strip()

        token_edit = QLineEdit()
        token_edit.setEchoMode(QLineEdit.EchoMode.Password)
        if existing_cfg and existing_cfg.source == "env":
            token_edit.setPlaceholderText("环境变量已配置（不回显）")
        elif existing_file_token:
            token_edit.setPlaceholderText("已保存本地Token（不回显，留空则保持）")
        else:
            token_edit.setPlaceholderText("输入OCR Token")

        sync_edit = QLineEdit()
        sync_edit.setText(self._ocr_cfg.sync_url if self._ocr_cfg else DEFAULT_SYNC_URL)
        job_edit = QLineEdit()
        job_edit.setText(self._ocr_cfg.job_url if self._ocr_cfg else DEFAULT_JOB_URL)
        model_edit = QLineEdit()
        model_edit.setText(self._ocr_cfg.model if self._ocr_cfg else DEFAULT_MODEL)
        retry_edit = QLineEdit()
        retry_edit.setText(str(self._ocr_cfg.retry_count if self._ocr_cfg else 1))
        backoff_edit = QLineEdit()
        backoff_edit.setText(str(self._ocr_cfg.retry_backoff_sec if self._ocr_cfg else 1.0))
        proxy_edit = QLineEdit()
        proxy_edit.setPlaceholderText("例如：http://127.0.0.1:7890")
        proxy_edit.setText(self._ocr_cfg.proxy_url if self._ocr_cfg else "")

        form.addRow("Token", token_edit)
        form.addRow("同步URL", sync_edit)
        form.addRow("异步JOB URL", job_edit)
        form.addRow("模型", model_edit)
        form.addRow("重试次数", retry_edit)
        form.addRow("重试退避(秒)", backoff_edit)
        form.addRow("代理URL", proxy_edit)
        insecure_chk = QCheckBox("SSL异常时允许不校验证书重试（兼容部分网络环境）")
        insecure_chk.setChecked(self._ocr_cfg.insecure_fallback if self._ocr_cfg else True)
        form.addRow("", insecure_chk)
        layout.addLayout(form)

        tip = QLabel(f"说明：环境变量优先于本地设置。\n本地配置保存路径：{self._ocr_config_path}")
        tip.setStyleSheet("color:#7f8c8d;font-size:12px;")
        layout.addWidget(tip)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addWidget(btn_box)

        if dlg.exec() != int(QDialog.DialogCode.Accepted):
            return

        token = token_edit.text().strip()
        if not token and existing_file_token:
            token = existing_file_token
        if not token:
            QMessageBox.warning(self, "OCR设置", "Token 不能为空。")
            return
        try:
            retry_count = max(1, int(retry_edit.text().strip() or "1"))
            retry_backoff = max(0.1, float(backoff_edit.text().strip() or "1.0"))
        except ValueError:
            QMessageBox.warning(self, "OCR设置", "重试次数或退避时间格式错误。")
            return

        cfg = OcrConfig(
            token=token,
            sync_url=sync_edit.text().strip() or DEFAULT_SYNC_URL,
            job_url=job_edit.text().strip() or DEFAULT_JOB_URL,
            model=model_edit.text().strip() or DEFAULT_MODEL,
            insecure_fallback=insecure_chk.isChecked(),
            retry_count=retry_count,
            retry_backoff_sec=retry_backoff,
            proxy_url=proxy_edit.text().strip(),
            source="file",
        )
        try:
            saved = cfg.save_to_file(self._ocr_config_path)
        except Exception as e:
            QMessageBox.critical(self, "OCR设置", f"保存失败：{e}")
            return

        # Environment variable still takes precedence; reload with merged policy.
        self._ocr_cfg = OcrConfig.load(self._ocr_config_path)
        self._ocr_client = None
        self._local_ocr_engine = None
        self._local_ocr_json_engine = None
        self._local_ocr_self_check = None
        self._local_ocr_self_check_key = None
        self._refresh_ocr_ui_state()
        storage_note = self._build_ocr_storage_note(cfg.token_storage)
        QMessageBox.information(
            self,
            "OCR设置",
            f"已保存到：{saved}\n{storage_note}\n如设置了 VERBATIM_OCR_TOKEN，运行时将优先使用环境变量。",
        )

    def _open_diagnostics(self) -> None:
        route = self._ocr_route_mode()
        mode = self._current_ocr_mode()
        token_source = self._ocr_cfg.source if self._ocr_cfg else "none"
        token_present = bool(self._ocr_cfg and self._ocr_cfg.token.strip())
        runtime_dir = self._ocr_runtime_dir()
        json_exe = self._ocr_json_exe_path()
        local_check = self._get_local_ocr_self_check(force=True)
        json_args = (os.getenv("VERBATIM_PADDLEOCR_JSON_ARGS") or "").strip()
        breaker_open = self._local_ocr_breaker_open()
        breaker_left = max(0, int(self._local_ocr_cooldown_until - time.monotonic())) if breaker_open else 0

        diag = {
            "trace_id": self._trace_id,
            "ocr_route": route,
            "ocr_mode": mode,
            "token_source": token_source,
            "token_present": token_present,
            "local_runtime_dir": str(runtime_dir) if runtime_dir else "",
            "local_paddleocr_json_exe": str(json_exe) if json_exe else "",
            "local_paddleocr_json_args": json_args,
            "local_ocr_self_check": {
                "available": bool(local_check.available),
                "code": str(local_check.code),
                "message": str(local_check.message),
                "worker_python": str(local_check.worker_python),
                "runtime_dir": str(local_check.runtime_dir),
                "json_exe": str(local_check.json_exe),
                "python_worker_ready": bool(local_check.python_worker_ready),
                "json_ready": bool(local_check.json_ready),
            },
            "local_breaker_open": breaker_open,
            "local_breaker_left_sec": breaker_left,
            "last_ocr_errors": list(self._last_ocr_errors or []),
            "last_ocr_error_codes": list(self._last_ocr_error_codes or []),
            "last_ocr_state": str(self._last_ocr_state),
            "last_ocr_state_reason": str(self._last_ocr_state_reason or ""),
            "last_compare_summary": str(self._last_compare_summary or ""),
        }
        log_event("DIAG_EXPORT", "diagnostic exported", level="info", trace_id=self._trace_id)

        dlg = QDialog(self)
        dlg.setWindowTitle("诊断信息")
        dlg.resize(720, 420)
        layout = QVBoxLayout(dlg)
        text = QTextEdit()
        text.setReadOnly(True)
        text.setText(json.dumps(diag, ensure_ascii=False, indent=2))
        layout.addWidget(text)
        btns = QDialogButtonBox()
        copy_btn = btns.addButton("复制", QDialogButtonBox.ButtonRole.AcceptRole)
        close_btn = btns.addButton(QDialogButtonBox.StandardButton.Close)
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(text.toPlainText()))
        close_btn.clicked.connect(dlg.reject)
        layout.addWidget(btns)
        dlg.exec()

    def _open_advanced_settings(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("高级设置")
        layout = QVBoxLayout(dlg)

        chk_show_format = QCheckBox("显示格式差异")
        chk_show_format.setChecked(self._btn_filter_format.isChecked())
        chk_pure_content = QCheckBox("忽略空白")
        chk_pure_content.setChecked(self._btn_pure_content.isChecked())
        chk_ignore_punct = QCheckBox("忽略标点差异")
        chk_ignore_punct.setChecked(self._btn_ignore_punctuation.isChecked())
        chk_norm_numbers = QCheckBox("数字标准化")
        chk_norm_numbers.setChecked(self._btn_normalize_numbers.isChecked())
        chk_merge_kv = QCheckBox("键值断行合并")
        chk_merge_kv.setChecked(self._btn_merge_keyvalue.isChecked())

        reading_combo = QComboBox()
        reading_combo.addItem("自动", "auto")
        reading_combo.addItem("原始", "raw")
        reading_combo.addItem("单栏", "single_column")
        reading_combo.addItem("双栏", "two_column")
        ridx = reading_combo.findData(self._current_reading_order_mode())
        reading_combo.setCurrentIndex(0 if ridx < 0 else ridx)

        ocr_mode_combo = QComboBox()
        ocr_mode_combo.addItem("同步", "sync")
        ocr_mode_combo.addItem("异步", "async")
        oidx = ocr_mode_combo.findData(self._current_ocr_mode())
        ocr_mode_combo.setCurrentIndex(0 if oidx < 0 else oidx)
        ocr_mode_combo.setEnabled(self._btn_use_ocr.isEnabled())

        form = QFormLayout()
        form.addRow("阅读顺序", reading_combo)
        form.addRow("比对选项", chk_show_format)
        form.addRow("", chk_pure_content)
        form.addRow("", chk_ignore_punct)
        form.addRow("", chk_norm_numbers)
        form.addRow("", chk_merge_kv)
        form.addRow("OCR模式", ocr_mode_combo)
        layout.addLayout(form)

        tip = QLabel("说明：普通用户保持默认即可；仅当结果异常时再调整。")
        tip.setStyleSheet("color:#7f8c8d;font-size:12px;")
        layout.addWidget(tip)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addWidget(btn_box)

        if dlg.exec() != int(QDialog.DialogCode.Accepted):
            return

        self._btn_filter_format.setChecked(chk_show_format.isChecked())
        self._toggle_format_diffs()
        self._btn_pure_content.setChecked(chk_pure_content.isChecked())
        self._on_pure_content_toggled()

        self._btn_ignore_punctuation.setChecked(chk_ignore_punct.isChecked())
        self._btn_normalize_numbers.setChecked(chk_norm_numbers.isChecked())
        self._btn_merge_keyvalue.setChecked(chk_merge_kv.isChecked())

        rmode = reading_combo.currentData()
        ridx2 = self._reading_order_combo.findData(rmode)
        if ridx2 >= 0:
            self._reading_order_combo.setCurrentIndex(ridx2)

        omode = ocr_mode_combo.currentData()
        oidx2 = self._ocr_mode_combo.findData(omode)
        if oidx2 >= 0:
            self._ocr_mode_combo.setCurrentIndex(oidx2)

    def _ensure_doc_profiles(self) -> tuple[DocumentProfile, DocumentProfile] | None:
        if self._left_pdf is None or self._right_pdf is None:
            QMessageBox.warning(self, "预对齐建议", "请先加载左右文档。")
            return None
        try:
            if self._left_doc_profile is None:
                self._left_doc_profile = build_document_profile(self._left_pdf)
            if self._right_doc_profile is None:
                self._right_doc_profile = build_document_profile(self._right_pdf)
        except Exception as e:
            QMessageBox.critical(self, "预对齐建议", f"构建文档画像失败：{e}")
            return None
        return self._left_doc_profile, self._right_doc_profile

    def _apply_selected_bboxes(self) -> None:
        if self._left_sel_bbox:
            z = self._render_zoom_for("left")
            left_rect = QRectF(
                self._left_sel_bbox[0] * z,
                self._left_sel_bbox[1] * z,
                (self._left_sel_bbox[2] - self._left_sel_bbox[0]) * z,
                (self._left_sel_bbox[3] - self._left_sel_bbox[1]) * z,
            )
            self.left_view.set_selected_overlays([(left_rect, QColor(52, 152, 219, 180))])
        if self._right_sel_bbox:
            z = self._render_zoom_for("right")
            right_rect = QRectF(
                self._right_sel_bbox[0] * z,
                self._right_sel_bbox[1] * z,
                (self._right_sel_bbox[2] - self._right_sel_bbox[0]) * z,
                (self._right_sel_bbox[3] - self._right_sel_bbox[1]) * z,
            )
            self.right_view.set_selected_overlays([(right_rect, QColor(46, 204, 113, 180))])

    @staticmethod
    def _bbox_changed(a: BBox | None, b: BBox | None, eps: float = 1.0) -> bool:
        if a is None or b is None:
            return bool(a is not b)
        return any(abs(float(x) - float(y)) > eps for x, y in zip(a, b))

    def _track_manual_adjust(self, side: str, bbox: BBox) -> None:
        if not self._prealign_active:
            return
        if side == "left":
            if not self._bbox_changed(self._prealign_last_left_bbox, bbox):
                return
            self._prealign_last_left_bbox = bbox
            base = self._prealign_base_left_bbox
        else:
            if not self._bbox_changed(self._prealign_last_right_bbox, bbox):
                return
            self._prealign_last_right_bbox = bbox
            base = self._prealign_base_right_bbox
        if self._bbox_changed(base, bbox):
            self._prealign_manual_adjust_steps += 1
            print(f"[verbatim] Prealign manual adjust +1: side={side}, steps={self._prealign_manual_adjust_steps}")

    def _open_prealign_suggestions(self) -> None:
        self._legacy_open_prealign_suggestions()
        return
        profiles = self._ensure_doc_profiles()
        if profiles is None:
            return
        left_doc, right_doc = profiles
        left_idx = int(self._left_page_number)
        candidates_map = retrieve_page_candidates(left_doc, right_doc, top_k=3, min_score=0.05)
        page_candidates = candidates_map.get(left_idx, [])
        if page_candidates:
            log_event(
                "PREALIGN_PAGE_CANDIDATES",
                "prealign page candidates",
                trace_id=self._trace_id,
                left_page=int(left_idx),
                candidates=[
                    {
                        "right_page": int(c.right_page),
                        "score": float(c.score),
                        "text_sim": float(c.text_sim),
                        "anchor_sim": float(c.anchor_sim),
                        "failure_type": str(c.failure_type),
                    }
                    for c in page_candidates
                ],
            )
        if not page_candidates:
            QMessageBox.information(self, "预对齐建议", "当前左页未找到可用候选。")
            return

        if self._left_pdf is None or self._right_pdf is None:
            return
        try:
            left_page = self._left_page if self._left_page is not None else parse_page(self._left_pdf, left_idx)
        except Exception as e:
            QMessageBox.critical(self, "预对齐建议", f"加载左页失败：{e}")
            return

        items_payload: list[tuple[int, int, BBox, BBox, float, str]] = []
        summary_lines: list[str] = []
        for idx, pg in enumerate(page_candidates, 1):
            failure_text = {
                "anchor_sparse": "锚点稀疏",
                "scanned_noise": "扫描噪声",
                "layout_conflict": "版式冲突",
                "low_similarity": "相似度低",
                "ok": "正常",
            }.get(pg.failure_type, pg.failure_type)
            summary_lines.append(
                f"{idx}) 右{pg.right_page + 1} | "
                f"score={pg.score:.2f} text_sim={pg.text_sim:.2f} "
                f"anchor_sim={pg.anchor_sim:.2f} failure={pg.failure_type}({failure_text})"
            )
        for pg in page_candidates:
            try:
                right_page = parse_page(self._right_pdf, int(pg.right_page))
            except Exception:
                continue
            region_cands = suggest_region_candidates(left_page, right_page, top_k=2)
            for rc in region_cands:
                failure_text = {
                    "anchor_sparse": "锚点稀疏",
                    "scanned_noise": "扫描噪声",
                    "layout_conflict": "版式冲突",
                    "low_similarity": "相似度低",
                    "ok": "正常",
                }.get(pg.failure_type, pg.failure_type)
                reason = (
                    f"{rc.reason}; "
                    f"页候选 score={pg.score:.2f} text_sim={pg.text_sim:.2f} "
                    f"anchor_sim={pg.anchor_sim:.2f} failure={pg.failure_type}({failure_text})"
                )
                items_payload.append(
                    (left_idx, int(pg.right_page), rc.left_bbox, rc.right_bbox, float(rc.score), reason)
                )

        if not items_payload:
            QMessageBox.information(self, "预对齐建议", "未生成区域候选，请继续手动框选。")
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("预对齐建议")
        dlg.resize(680, 420)
        layout = QVBoxLayout(dlg)
        tip = QLabel("请选择一个候选并应用。建议先查看分数和理由，再点击“应用候选”。")
        tip.setStyleSheet("color:#5b6775;font-size:12px;")
        layout.addWidget(tip)
        if summary_lines:
            summary = QLabel("Top-K 页候选：\n" + "\n".join(summary_lines))
            summary.setWordWrap(True)
            summary.setStyleSheet(
                "color:#2c3e50;font-size:12px;background:#f6f8fa;border:1px solid #e5e7eb;padding:6px;"
            )
            layout.addWidget(summary)
        lst = QListWidget()
        for idx, p in enumerate(items_payload, 1):
            li, ri, _lb, _rb, s, reason = p
            txt = f"{idx}. 左{li + 1}页 -> 右{ri + 1}页 | 区域分={s:.2f} | {reason}"
            item = QListWidgetItem(txt)
            item.setData(Qt.ItemDataRole.UserRole, idx - 1)
            lst.addItem(item)
        if lst.count() > 0:
            lst.setCurrentRow(0)
        layout.addWidget(lst)

        btns = QDialogButtonBox()
        apply_btn = btns.addButton("应用候选", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel_btn = btns.addButton(QDialogButtonBox.StandardButton.Cancel)
        apply_btn.clicked.connect(dlg.accept)
        cancel_btn.clicked.connect(dlg.reject)
        layout.addWidget(btns)

        if dlg.exec() != int(QDialog.DialogCode.Accepted):
            return
        cur = lst.currentItem()
        if cur is None:
            QMessageBox.information(self, "预对齐建议", "未选择候选。")
            return
        idx = int(cur.data(Qt.ItemDataRole.UserRole))
        left_p, right_p, left_bbox, right_bbox, score, reason = items_payload[idx]
        self._apply_prealign_candidate(left_p, right_p, left_bbox, right_bbox, score, reason)

    def _legacy_ensure_doc_profiles(self) -> tuple[DocumentProfile, DocumentProfile] | None:
        if self._left_pdf is None or self._right_pdf is None:
            QMessageBox.warning(self, "预对齐建议", "请先加载左右文档。")
            return None
        try:
            if self._left_doc_profile is None:
                self._left_doc_profile = self._run_process_task_with_ui_pump(
                    "build_document_profile",
                    str(self._left_pdf),
                    timeout_ms=max(int(self._bg_task_timeout_ms), 15000),
                )
            if self._right_doc_profile is None:
                self._right_doc_profile = self._run_process_task_with_ui_pump(
                    "build_document_profile",
                    str(self._right_pdf),
                    timeout_ms=max(int(self._bg_task_timeout_ms), 15000),
                )
        except Exception as e:
            QMessageBox.critical(self, "预对齐建议", f"构建文档画像失败: {e}")
            return None
        return self._left_doc_profile, self._right_doc_profile

    def _legacy_open_prealign_suggestions(self) -> None:
        profiles = self._legacy_ensure_doc_profiles()
        if profiles is None:
            return
        left_idx = int(self._left_page_number)
        try:
            payload: PrealignComputationResult = self._run_process_task_with_ui_pump(
                "compute_prealign_payload",
                str(self._left_pdf),
                str(self._right_pdf),
                int(left_idx),
                top_k_pages=3,
                min_score=0.05,
                top_k_regions=2,
                timeout_ms=max(int(self._bg_task_timeout_ms), 20000),
            )
        except Exception as e:
            QMessageBox.critical(self, "预对齐建议", f"生成候选失败: {e}")
            return

        page_candidates = payload.page_candidates
        if page_candidates:
            log_event(
                "PREALIGN_PAGE_CANDIDATES",
                "prealign page candidates",
                trace_id=self._trace_id,
                left_page=int(left_idx),
                candidates=[
                    {
                        "right_page": int(c.right_page),
                        "score": float(c.score),
                        "text_sim": float(c.text_sim),
                        "anchor_sim": float(c.anchor_sim),
                        "failure_type": str(c.failure_type),
                    }
                    for c in page_candidates
                ],
            )
        if not page_candidates:
            QMessageBox.information(self, "预对齐建议", "当前左页未找到可用候选。")
            return

        items_payload = list(payload.items_payload)
        if not items_payload:
            QMessageBox.information(self, "预对齐建议", "未生成区域候选，请继续手动框选。")
            return

        summary_lines: list[str] = []
        for idx, pg in enumerate(page_candidates, 1):
            failure_text = {
                "anchor_sparse": "锚点稀疏",
                "scanned_noise": "扫描噪声",
                "layout_conflict": "版式冲突",
                "low_similarity": "相似度低",
                "ok": "正常",
            }.get(pg.failure_type, pg.failure_type)
            summary_lines.append(
                f"{idx}) 右页{pg.right_page + 1} | "
                f"score={pg.score:.2f} text_sim={pg.text_sim:.2f} "
                f"anchor_sim={pg.anchor_sim:.2f} failure={pg.failure_type}({failure_text})"
            )

        dlg = QDialog(self)
        dlg.setWindowTitle("预对齐建议")
        dlg.resize(680, 420)
        layout = QVBoxLayout(dlg)
        tip = QLabel("请选择一个候选并应用。建议先查看分数和原因，再点击“应用候选”。")
        tip.setStyleSheet("color:#5b6775;font-size:12px;")
        layout.addWidget(tip)
        summary = QLabel("Top-K 页候选：\n" + "\n".join(summary_lines))
        summary.setWordWrap(True)
        summary.setStyleSheet(
            "color:#2c3e50;font-size:12px;background:#f6f8fa;border:1px solid #e5e7eb;padding:6px;"
        )
        layout.addWidget(summary)
        lst = QListWidget()
        for idx, p in enumerate(items_payload, 1):
            li, ri, _lb, _rb, s, reason = p
            txt = f"{idx}. 左页{li + 1} -> 右页{ri + 1} | 区域分={s:.2f} | {reason}"
            item = QListWidgetItem(txt)
            item.setData(Qt.ItemDataRole.UserRole, idx - 1)
            lst.addItem(item)
        lst.setCurrentRow(0)
        layout.addWidget(lst)

        btns = QDialogButtonBox()
        apply_btn = btns.addButton("应用候选", QDialogButtonBox.ButtonRole.AcceptRole)
        cancel_btn = btns.addButton(QDialogButtonBox.StandardButton.Cancel)
        apply_btn.clicked.connect(dlg.accept)
        cancel_btn.clicked.connect(dlg.reject)
        layout.addWidget(btns)

        if dlg.exec() != int(QDialog.DialogCode.Accepted):
            return
        cur = lst.currentItem()
        if cur is None:
            QMessageBox.information(self, "预对齐建议", "未选择候选。")
            return
        idx = int(cur.data(Qt.ItemDataRole.UserRole))
        left_p, right_p, left_bbox, right_bbox, score, reason = items_payload[idx]
        self._apply_prealign_candidate(left_p, right_p, left_bbox, right_bbox, score, reason)

    def _apply_prealign_candidate(
        self,
        left_page_idx: int,
        right_page_idx: int,
        left_bbox: BBox,
        right_bbox: BBox,
        score: float,
        reason: str,
    ) -> None:
        if self._left_pdf is None or self._right_pdf is None:
            return

        if left_page_idx != self._left_page_number:
            self._set_page_index("left", left_page_idx)
        if right_page_idx != self._right_page_number:
            self._set_page_index("right", right_page_idx)

        self._left_sel_bbox = left_bbox
        self._right_sel_bbox = right_bbox
        self._prealign_active = True
        self._prealign_manual_adjust_steps = 0
        self._prealign_base_left_bbox = left_bbox
        self._prealign_base_right_bbox = right_bbox
        self._prealign_last_left_bbox = left_bbox
        self._prealign_last_right_bbox = right_bbox
        self._apply_selected_bboxes()
        self._update_compare_enabled()
        self._set_result_summary(
            f"已应用预对齐候选（分数 {score:.2f}），可直接比对或微调框选",
            warn=False,
        )
        print(f"[verbatim] Prealign candidate applied: {reason}")

    def _get_ocr_client(self) -> PaddleOcrClient | None:
        if self._ocr_client is not None:
            return self._ocr_client
        if self._ocr_cfg is None:
            self._ocr_cfg = OcrConfig.load(self._ocr_config_path)
        if self._ocr_cfg is None:
            return None
        self._ocr_client = PaddleOcrClient(self._ocr_cfg)
        return self._ocr_client

    @staticmethod
    def _ocr_route_mode() -> str:
        route = (os.getenv("VERBATIM_OCR_ROUTE") or "local_first").strip().lower()
        if route not in {"local_first", "cloud_only", "local_only"}:
            return "local_first"
        return route

    @staticmethod
    def _ocr_runtime_dir() -> Path | None:
        return resolve_ocr_runtime_dir()

    @staticmethod
    def _ocr_json_exe_path() -> Path | None:
        return resolve_ocr_json_exe_path()

    def _get_local_ocr_self_check(self, *, force: bool = False) -> LocalOcrSelfCheck:
        runtime_dir = self._ocr_runtime_dir()
        json_exe = self._ocr_json_exe_path()
        strict_offline = str(os.getenv("VERBATIM_OCR_OFFLINE_STRICT", "1")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        worker_python = LocalPaddleEngine.resolve_worker_python()
        key = (
            str(runtime_dir.resolve()) if runtime_dir is not None else "",
            str(json_exe.resolve()) if json_exe is not None else "",
            bool(strict_offline),
            str(worker_python),
        )
        if force or self._local_ocr_self_check is None or self._local_ocr_self_check_key != key:
            self._local_ocr_self_check = run_local_ocr_self_check(
                runtime_dir=runtime_dir,
                offline_strict=bool(strict_offline),
                json_exe=json_exe,
                worker_python=worker_python,
            )
            self._local_ocr_self_check_key = key
        return self._local_ocr_self_check

    def _get_local_ocr_engine(self) -> LocalPaddleEngine | None:
        runtime_dir = self._ocr_runtime_dir()
        if runtime_dir is None:
            return None
        if self._local_ocr_engine is None:
            strict_offline = str(os.getenv("VERBATIM_OCR_OFFLINE_STRICT", "1")).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            self._local_ocr_engine = LocalPaddleEngine(
                runtime_dir=runtime_dir,
                offline_strict=strict_offline,
            )
        return self._local_ocr_engine

    def _get_local_json_ocr_engine(self) -> LocalPaddleOcrJsonEngine | None:
        exe_path = self._ocr_json_exe_path()
        if exe_path is None:
            return None
        if self._local_ocr_json_engine is None:
            extra_args = (os.getenv("VERBATIM_PADDLEOCR_JSON_ARGS") or "").strip()
            self._local_ocr_json_engine = LocalPaddleOcrJsonEngine(
                exe_path=exe_path,
                extra_args=extra_args,
            )
        return self._local_ocr_json_engine

    def _get_cloud_ocr_engine(self) -> CloudPaddleEngine | None:
        client = self._get_ocr_client()
        if client is None:
            return None
        return CloudPaddleEngine(client)

    def _local_ocr_breaker_open(self) -> bool:
        return time.monotonic() < float(self._local_ocr_cooldown_until)

    def _record_local_ocr_success(self) -> None:
        self._local_ocr_fail_streak = 0
        self._local_ocr_cooldown_until = 0.0
        self._local_ocr_breaker_warned = False

    def _record_local_ocr_failure(self, err: Exception) -> None:
        self._local_ocr_fail_streak += 1
        if self._local_ocr_fail_streak < self._local_ocr_fail_threshold:
            return
        self._local_ocr_cooldown_until = time.monotonic() + float(self._local_ocr_cooldown_sec)
        self._local_ocr_breaker_warned = False
        log_event(
            "OCR_LOCAL_BREAKER_OPEN",
            "local ocr breaker opened",
            level="warning",
            trace_id=self._trace_id,
            fail_streak=int(self._local_ocr_fail_streak),
            cooldown_sec=int(self._local_ocr_cooldown_sec),
            error=str(err),
        )

    def _resolve_ocr_engines(self) -> list[tuple[str, OcrEngine]]:
        route = self._ocr_route_mode()
        cloud = self._get_cloud_ocr_engine()
        local_enabled = not self._local_ocr_breaker_open()
        local_check = self._get_local_ocr_self_check()
        local_json = self._get_local_json_ocr_engine() if local_enabled and local_check.json_ready else None
        local = self._get_local_ocr_engine() if local_enabled and local_check.python_worker_ready else None
        if not local_enabled and not self._local_ocr_breaker_warned:
            remain = max(0, int(self._local_ocr_cooldown_until - time.monotonic()))
            msg = f"[verbatim] Local OCR breaker open: cooldown {remain}s, skip local engine."
            print(msg)
            log_event(
                "OCR_LOCAL_BREAKER_SKIP",
                "local ocr skipped due to breaker",
                level="warning",
                trace_id=self._trace_id,
                cooldown_left_sec=remain,
            )
            self._local_ocr_breaker_warned = True
        if route in {"local_first", "local_only"} and not local_enabled:
            return [("cloud", cloud)] if route == "local_first" and cloud is not None else []
        if route in {"local_first", "local_only"} and not local_check.available:
            msg = f"[verbatim] Local OCR self-check blocked: {local_check.code}: {local_check.message}"
            print(msg)
            log_event(
                "OCR_LOCAL_SELF_CHECK_BLOCKED",
                "local ocr self-check blocked engine resolution",
                level="warning",
                trace_id=self._trace_id,
                reason_code=str(local_check.code),
                reason_detail=str(local_check.message),
            )
            if route == "local_only":
                return []
            return [("cloud", cloud)] if cloud is not None else []
        if route == "cloud_only":
            return [("cloud", cloud)] if cloud is not None else []
        if route == "local_only":
            local_only_engines: list[tuple[str, OcrEngine]] = []
            if local_json is not None:
                local_only_engines.append(("local_json", local_json))
            if local is not None:
                local_only_engines.append(("local", local))
            return local_only_engines
        # local_first
        engines: list[tuple[str, OcrEngine]] = []
        if local_json is not None:
            engines.append(("local_json", local_json))
        if local is not None:
            engines.append(("local", local))
        if cloud is not None:
            engines.append(("cloud", cloud))
        return engines

    def _build_region_from_text(self, text: str, page_number: int) -> RegionData:
        chars: list[CharData] = []
        x = 0.0
        y = 0.0
        for i, ch in enumerate(text):
            if ch == "\n":
                y += 1.0
                x = 0.0
                continue
            chars.append(
                CharData(
                    char=ch,
                    index=i,
                    bbox=(x, y, x + 1.0, y + 1.0),
                    font_name="OCR",
                    font_family="OCR",
                    size=10.0,
                    color_rgb=(0, 0, 0),
                    style=StyleFlags(),
                )
            )
            x += 1.0
        return RegionData(page_number=page_number, bboxes=[], chars=chars)

    def _build_region_from_ocr_spans(self, spans: list[OcrSpan], page_number: int) -> RegionData:
        chars: list[CharData] = []
        idx = 0
        for span in spans:
            text = span.text
            bbox = span.bbox
            if not text:
                continue
            x0, y0, x1, y1 = [float(v) for v in bbox]
            width = max(1.0, x1 - x0)
            step = width / max(1, len(text))
            for ch_i, ch in enumerate(text):
                if ch == "\n":
                    continue
                cx0 = x0 + step * ch_i
                cx1 = x0 + step * (ch_i + 1)
                chars.append(
                    CharData(
                        char=ch,
                        index=idx,
                        bbox=(cx0, y0, cx1, y1),
                        font_name="OCR_BOX",
                        font_family="OCR_BOX",
                        size=10.0,
                        color_rgb=(0, 0, 0),
                        style=StyleFlags(),
                    )
                )
                idx += 1
        return RegionData(page_number=page_number, bboxes=[], chars=chars)

    @staticmethod
    def _region_uses_synthetic_coords(region: RegionData | None) -> bool:
        if region is None or not region.chars:
            return False
        sample = region.chars[: min(16, len(region.chars))]
        return all((str(ch.font_name) == "OCR" and str(ch.font_family) == "OCR") for ch in sample)

    @staticmethod
    def _region_uses_ocr_boxes(region: RegionData | None) -> bool:
        if region is None or not region.chars:
            return False
        sample = region.chars[: min(16, len(region.chars))]
        return all((str(ch.font_name) == "OCR_BOX" and str(ch.font_family) == "OCR_BOX") for ch in sample)

    def _ocr_candidate_score(
        self,
        text: str,
        *,
        reference_text: str = "",
        peer_text: str = "",
    ) -> tuple[float, dict[str, float]]:
        q = self._check_text_quality(text)
        garble_score, _ = self._garble_signal_score(text)
        score = float(q.get("confidence", 0)) - garble_score * 15.0
        if str(q.get("quality", "good")) == "bad":
            score -= 30.0
        elif str(q.get("quality", "good")) == "warning":
            score -= 8.0

        # Penalize malformed fragments that often appear in OCR noise.
        weird_tokens = len(re.findall(r"[A-Za-z]\d|\d[A-Za-z]|[A-Za-z]{1,2}/[A-Za-z]{1,2}", text or ""))
        score -= weird_tokens * 1.5

        # Penalize repeated lines (common parent/child duplicated extraction side effects).
        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
        if lines:
            unique = len(set(lines))
            duplicate_ratio = 1.0 - (unique / len(lines))
            score -= duplicate_ratio * 15.0
        else:
            duplicate_ratio = 0.0

        if reference_text.strip():
            ref_len = max(1, len(reference_text.strip()))
            len_delta = abs(len((text or "").strip()) - ref_len) / ref_len
            score -= min(25.0, len_delta * 35.0)
        else:
            len_delta = 0.0

        if peer_text.strip():
            peer_sim = self._normalized_similarity(text, peer_text)
            score += peer_sim * 8.0
        else:
            peer_sim = 0.0

        details = {
            "confidence": float(q.get("confidence", 0)),
            "garble_score": float(garble_score),
            "weird_tokens": float(weird_tokens),
            "duplicate_ratio": float(duplicate_ratio),
            "len_delta": float(len_delta),
            "peer_sim": float(peer_sim),
        }
        return score, details

    @staticmethod
    def _looks_like_path_text(text: str) -> bool:
        t = (text or "").strip()
        if not t:
            return True
        tl = t.lower()
        if tl.startswith(("http://", "https://", "file://", "data:image/")):
            return True
        if any(x in tl for x in ("/sdk_storage/", "\\sdk_storage\\", "/resources/images/", "\\resources\\images\\")):
            return True
        if re.search(r"(?:^|[\\/])img_v\d+_[^\\/\s]+\.(?:jpg|jpeg|png|webp|bmp)$", tl):
            return True
        if ("/" in t or "\\" in t) and re.search(r"\.(?:jpg|jpeg|png|webp|bmp|gif|svg)$", tl):
            return True
        return False

    @classmethod
    def _sanitize_ocr_text(cls, text: str) -> str:
        """Remove path/url-like noise fragments from OCR output text."""
        t = (text or "").strip()
        if not t:
            return ""

        # Remove explicit URL tokens.
        t = re.sub(r"https?://\S+", " ", t, flags=re.IGNORECASE)
        t = re.sub(r"file://\S+", " ", t, flags=re.IGNORECASE)

        # Remove Windows/Unix image path fragments.
        t = re.sub(
            r"(?:[A-Za-z]:)?[\\/](?:[^\\/\s]+[\\/])*(?:resources[\\/]images|sdk_storage)[^\\/\s]*[\\/][^\\/\s]+\.(?:jpg|jpeg|png|webp|bmp|gif|svg)",
            " ",
            t,
            flags=re.IGNORECASE,
        )

        # Drop path-like lines entirely.
        kept_lines: list[str] = []
        for ln in t.splitlines():
            s = ln.strip()
            if not s:
                continue
            if cls._looks_like_path_text(s):
                continue
            kept_lines.append(s)

        if not kept_lines:
            return ""

        cleaned = "\n".join(kept_lines)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    @staticmethod
    def _expand_bbox_for_ocr(bbox: BBox, *, ratio: float = 0.18) -> BBox:
        x0, y0, x1, y1 = [float(v) for v in bbox]
        w = max(1.0, x1 - x0)
        h = max(1.0, y1 - y0)
        dx = w * ratio
        dy = h * ratio
        return (x0 - dx, y0 - dy, x1 + dx, y1 + dy)

    @staticmethod
    def _ocr_cache_bbox_key(bbox: BBox) -> tuple[int, int, int, int]:
        # Quantize to quarter-point precision so tiny jitter is ignored without collapsing nearby selections.
        scale = 4.0
        return tuple(int(round(float(v) * scale)) for v in bbox)  # type: ignore[return-value]

    def _ocr_cache_key(
        self, pdf_path: Path, page_number: int, bbox: BBox, side_label: str
    ) -> tuple[str, int, tuple[int, int, int, int], str]:
        try:
            return (
                str(pdf_path.resolve()),
                int(page_number),
                self._ocr_cache_bbox_key(bbox),
                str(side_label),
            )
        except Exception:
            return (
                str(pdf_path),
                int(page_number),
                self._ocr_cache_bbox_key(bbox),
                str(side_label),
            )

    def _get_cached_ocr_spans(self, cache_key: tuple[str, int, tuple[int, int, int, int], str]) -> list[OcrSpan]:
        return list(self._ocr_result_spans_cache.get(cache_key, []))

    def _ocr_cached_coords_reliable(self, cache_key: tuple[str, int, tuple[int, int, int, int], str]) -> bool:
        meta = self._ocr_result_spans_meta.get(cache_key) or {}
        if "coords_reliable" in meta:
            return bool(meta.get("coords_reliable"))
        return bool(self._ocr_result_spans_cache.get(cache_key))

    def _store_ocr_cache_result(
        self,
        *,
        cache_key: tuple[str, int, tuple[int, int, int, int], str],
        pdf_path: Path,
        page_number: int,
        result: OcrResult,
        clip_bbox: BBox,
        source_bbox: BBox,
        coords_reliable: bool,
    ) -> None:
        self._ocr_result_cache[cache_key] = result
        self._ocr_result_spans_meta[cache_key] = {
            "pdf": str(pdf_path),
            "page": int(page_number),
            "clip_bbox": list(clip_bbox),
            "source_bbox": list(source_bbox),
            "coords_reliable": bool(coords_reliable),
        }
        if coords_reliable and result.spans:
            self._ocr_result_spans_cache[cache_key] = list(result.spans)
            return
        self._ocr_result_spans_cache.pop(cache_key, None)

    def _run_in_background_with_ui_pump(self, fn, *args, timeout_ms: int | None = None, **kwargs):
        """Run blocking callable in a daemon thread while pumping UI events."""
        holder: dict[str, object] = {}
        done = threading.Event()

        def _target() -> None:
            try:
                holder["result"] = fn(*args, **kwargs)
                holder["error"] = None
            except Exception as e:
                holder["result"] = None
                holder["error"] = e
            finally:
                done.set()

        t = threading.Thread(target=_target, name="verbatim-bg-task", daemon=True)
        t.start()
        timeout_ms_final = max(1000, int(timeout_ms if timeout_ms is not None else self._bg_task_timeout_ms))
        deadline = time.monotonic() + timeout_ms_final / 1000.0

        while not done.wait(0.03):
            QApplication.processEvents()
            if time.monotonic() >= deadline:
                timeout_sec = max(1, int(timeout_ms_final // 1000))
                raise TimeoutError(f"Background task timed out (>{timeout_sec}s)")

        err = holder.get("error")
        if err is not None:
            raise err  # type: ignore[misc]
        return holder.get("result")

    def _run_process_task_with_ui_pump(self, task_name: str, *args, timeout_ms: int | None = None, **kwargs):
        timeout_ms_final = max(200, int(timeout_ms if timeout_ms is not None else self._bg_task_timeout_ms))
        payload = {"args": args, "kwargs": kwargs}
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as input_file:
            input_path = Path(input_file.name)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as output_file:
            output_path = Path(output_file.name)
        input_path.write_bytes(pickle.dumps(payload))
        try:
            py_exec = str(os.getenv("VERBATIM_BG_WORKER_PYTHON", "")).strip()
            if py_exec:
                cmd = [
                    py_exec,
                    "-m",
                    "core.services.background_worker",
                    "--task",
                    str(task_name),
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                ]
            else:
                exe_name = Path(sys.executable).stem.lower()
                if "python" in exe_name:
                    cmd = [
                        sys.executable,
                        "-m",
                        "core.services.background_worker",
                        "--task",
                        str(task_name),
                        "--input",
                        str(input_path),
                        "--output",
                        str(output_path),
                    ]
                else:
                    cmd = [
                        sys.executable,
                        "--background-task-worker",
                        "--task",
                        str(task_name),
                        "--input",
                        str(input_path),
                        "--output",
                        str(output_path),
                    ]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=False,
            )
            deadline = time.monotonic() + timeout_ms_final / 1000.0
            while proc.poll() is None:
                QApplication.processEvents()
                if time.monotonic() >= deadline:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=1.0)
                    timeout_sec = max(1, int(timeout_ms_final // 1000))
                    raise TimeoutError(f"Background process task timed out (>{timeout_sec}s): {task_name}")
                time.sleep(0.03)
            stdout, stderr = proc.communicate()
            if proc.returncode != 0:
                detail = (stderr or stdout or b"").decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"Background process task failed ({task_name}): {detail}")
            if not output_path.exists() or output_path.stat().st_size == 0:
                raise RuntimeError(f"Background process task produced no output: {task_name}")
            return pickle.loads(output_path.read_bytes())
        finally:
            input_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)

    def _try_ocr_text(
        self,
        pdf_path: Path,
        page_number: int,
        bbox: BBox,
        side_label: str,
        baseline_text: str = "",
        peer_text: str = "",
        _expanded_retry: bool = False,
        _origin_cache_key: tuple[str, int, tuple[int, int, int, int], str] | None = None,
    ) -> str:
        engines = self._resolve_ocr_engines()
        if not engines:
            return ""
        cache_key = self._ocr_cache_key(pdf_path, page_number, bbox, side_label)
        effective_cache_key = _origin_cache_key or cache_key
        cached_result = self._ocr_result_cache.get(effective_cache_key)
        if cached_result:
            print(f"[verbatim] OCR cache hit for {side_label}: len={len(cached_result.text)}")
            return cached_result.text
        try:
            mode = self._current_ocr_mode()
            route = self._ocr_route_mode()
            print(f"[verbatim] OCR route: {route}")
            log_event(
                "OCR_START",
                "ocr started",
                trace_id=self._trace_id,
                side=side_label,
                route=route,
                mode=mode,
                bbox=[float(x) for x in bbox],
                page=int(page_number),
                baseline=summarize_text(baseline_text),
                peer=summarize_text(peer_text),
                expanded_retry=bool(_expanded_retry),
            )
            allow_sync_to_async_retry = str(os.getenv("VERBATIM_OCR_SYNC_FALLBACK_ASYNC", "0")).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            variants = [
                {"zoom": 3.0, "padding": 2.0, "grayscale": False},
                {"zoom": 4.0, "padding": 4.0, "grayscale": True},
            ]
            best_result: OcrResult | None = None
            best_score = -1e9
            sync_to_async_used = False
            cloud_empty_file_seen = False
            cloud_timeout_seen = False
            ocr_started = time.monotonic()
            max_ocr_seconds = float(os.getenv("VERBATIM_OCR_BUDGET_SEC", "25") or "25")
            last_clip_bbox: BBox | None = None
            last_zoom: float | None = None
            last_image_len = 0
            best_clip_bbox: BBox | None = None

            for i, v in enumerate(variants, start=1):
                if time.monotonic() - ocr_started > max_ocr_seconds:
                    print(f"[verbatim] OCR({side_label}) aborted: exceeded {max_ocr_seconds:.0f}s budget")
                    break
                rendered = self._run_process_task_with_ui_pump(
                    "render_region_with_meta",
                    str(pdf_path),
                    int(page_number),
                    bbox,
                    zoom=float(v["zoom"]),
                    padding=float(v["padding"]),
                    grayscale=bool(v["grayscale"]),
                    timeout_ms=int(self._bg_task_timeout_ms),
                )
                image_bytes = rendered.image_bytes
                clip_x0, clip_y0, clip_x1, clip_y1 = rendered.clip_bbox
                zoom_used = rendered.zoom
                last_clip_bbox = rendered.clip_bbox
                last_zoom = zoom_used
                last_image_len = len(image_bytes or b"")
                input_issues = validate_ocr_input(
                    pdf_path=pdf_path,
                    page_number=page_number,
                    bbox=bbox,
                    image_bytes=image_bytes,
                    clip_bbox=rendered.clip_bbox,
                    zoom=float(zoom_used),
                )
                log_event(
                    "OCR_RENDERED",
                    "ocr region rendered",
                    trace_id=self._trace_id,
                    side=side_label,
                    variant=int(i),
                    zoom=float(zoom_used),
                    padding=float(v["padding"]),
                    grayscale=bool(v["grayscale"]),
                    image_bytes=int(last_image_len),
                    clip_bbox=[float(clip_x0), float(clip_y0), float(clip_x1), float(clip_y1)],
                    issues=list(input_issues),
                )
                if not image_bytes or len(image_bytes) < 64:
                    print(f"[verbatim] OCR({side_label}) variant#{i}: skipped empty rendered image bytes")
                    continue
                print(
                    f"[verbatim] OCR({side_label}) variant#{i}: image_bytes={len(image_bytes)} "
                    f"bbox=({bbox[0]:.1f},{bbox[1]:.1f},{bbox[2]:.1f},{bbox[3]:.1f})"
                )

                try:
                    remaining_sec = max_ocr_seconds - (time.monotonic() - ocr_started)
                    if remaining_sec <= 0:
                        print(f"[verbatim] OCR({side_label}) skip route: no budget left")
                        continue
                    call_timeout_ms = max(1000, int(min(float(self._bg_task_timeout_ms), remaining_sec * 1000.0)))
                    result = None
                    candidate_spans: list[OcrSpan] = []
                    candidate_spans_pdf: list[OcrSpan] = []
                    mode_for_log = ""
                    engine_name = ""
                    last_engine_err: Exception | None = None
                    for engine_label, engine in engines:
                        try:
                            log_event(
                                "OCR_ENGINE_ATTEMPT",
                                "ocr engine attempt",
                                trace_id=self._trace_id,
                                side=side_label,
                                variant=int(i),
                                engine=str(engine_label),
                                mode=str(mode),
                                timeout_ms=int(call_timeout_ms),
                            )
                            local_min_timeout_ms = int(os.getenv("VERBATIM_LOCAL_OCR_TIMEOUT_MS", "15000") or "15000")
                            eff_timeout_ms = call_timeout_ms
                            if engine_label.startswith("local"):
                                eff_timeout_ms = max(call_timeout_ms, local_min_timeout_ms)
                            result = engine.recognize(
                                image_bytes=image_bytes,
                                filename=f"{side_label}_region.png",
                                mode=mode,
                                run_bg=self._run_in_background_with_ui_pump,
                                timeout_ms=eff_timeout_ms,
                                allow_sync_to_async_retry=(allow_sync_to_async_retry and not sync_to_async_used),
                            )
                            engine_name = engine_label
                            mode_for_log = result.mode
                            if engine_label.startswith("local"):
                                self._record_local_ocr_success()
                            if result.mode == "sync->async":
                                sync_to_async_used = True
                            log_event(
                                "OCR_ENGINE_OK",
                                "ocr engine succeeded",
                                trace_id=self._trace_id,
                                side=side_label,
                                variant=int(i),
                                engine=str(engine_name),
                                mode=str(mode_for_log),
                                text=summarize_text(result.text or ""),
                            )
                            if getattr(result, "spans", None):
                                candidate_spans = list(result.spans or ())
                                for span in candidate_spans:
                                    t = span.text
                                    b = span.bbox
                                    bx0, by0, bx1, by1 = [float(x) for x in b]
                                    pdf_x0 = clip_x0 + bx0 / zoom_used
                                    pdf_y0 = clip_y0 + by0 / zoom_used
                                    pdf_x1 = clip_x0 + bx1 / zoom_used
                                    pdf_y1 = clip_y0 + by1 / zoom_used
                                    pdf_x0 = max(clip_x0, min(pdf_x0, clip_x1))
                                    pdf_y0 = max(clip_y0, min(pdf_y0, clip_y1))
                                    pdf_x1 = max(clip_x0, min(pdf_x1, clip_x1))
                                    pdf_y1 = max(clip_y0, min(pdf_y1, clip_y1))
                                    candidate_spans_pdf.append(OcrSpan(text=t, bbox=(pdf_x0, pdf_y0, pdf_x1, pdf_y1)))
                            break
                        except Exception as engine_err:
                            last_engine_err = engine_err
                            print(f"[verbatim] OCR({engine_label}) failed on {side_label}, variant#{i}: {engine_err}")
                            if engine_label.startswith("local"):
                                self._record_local_ocr_failure(engine_err)
                            if engine_label == "cloud":
                                if "空文件" in str(engine_err):
                                    cloud_empty_file_seen = True
                                if "timed out" in str(engine_err).lower() or "timeout" in str(engine_err).lower():
                                    cloud_timeout_seen = True
                            log_event(
                                "OCR_ENGINE_FAIL",
                                "ocr engine failed",
                                level="warning",
                                trace_id=self._trace_id,
                                side=side_label,
                                variant=int(i),
                                engine=str(engine_label),
                                error=str(engine_err),
                            )
                            continue
                    if result is None:
                        assert last_engine_err is not None
                        raise last_engine_err
                except Exception as variant_err:
                    print(f"[verbatim] OCR failed on {side_label}, variant#{i}: {variant_err}")
                    if "空文件" in str(variant_err):
                        cloud_empty_file_seen = True
                    if "timed out" in str(variant_err).lower() or "timeout" in str(variant_err).lower():
                        cloud_timeout_seen = True
                    self._record_ocr_error(side_label, variant_err, variant=i)
                    log_event(
                        "OCR_VARIANT_FAIL",
                        "ocr variant failed",
                        level="warning",
                        trace_id=self._trace_id,
                        side=side_label,
                        route=self._ocr_route_mode(),
                        mode=self._current_ocr_mode(),
                        variant=i,
                        error=str(variant_err),
                    )
                    continue

                raw_text = (result.text or "").strip()
                text = self._sanitize_ocr_text(raw_text)
                spans_for_result = tuple(candidate_spans_pdf or candidate_spans)
                ocr_result = OcrResult(
                    text=text,
                    raw_text=raw_text,
                    spans=spans_for_result,
                )
                validated = validate_ocr_result(
                    ocr_result,
                    baseline_text=baseline_text,
                    peer_text=peer_text,
                )
                log_event(
                    "OCR_POSTPROCESS",
                    "ocr postprocess applied",
                    trace_id=self._trace_id,
                    side=side_label,
                    variant=int(i),
                    before=summarize_text(raw_text),
                    after=summarize_text(validated.result.text),
                )
                if self._looks_like_path_text(validated.result.text):
                    print(
                        f"[verbatim] OCR({engine_name}:{mode_for_log}) "
                        f"{side_label} variant#{i}: ignored non-text payload"
                    )
                    continue
                if validated.status.has_error:
                    log_event(
                        "OCR_CANDIDATE_INVALID",
                        "ocr candidate rejected by validation",
                        level="warning",
                        trace_id=self._trace_id,
                        side=side_label,
                        variant=int(i),
                        engine=str(engine_name),
                        mode=str(mode_for_log),
                        route=self._ocr_route_mode(),
                        issues=list(validated.status.codes),
                    )
                    continue
                q = self._check_text_quality(validated.result.text)
                garble_score, _ = self._garble_signal_score(validated.result.text)
                score, details = self._ocr_candidate_score(
                    validated.result.text,
                    reference_text=baseline_text,
                    peer_text=peer_text,
                )
                score -= float(validated.status.penalty)

                print(
                    f"[verbatim] OCR({engine_name}:{mode_for_log}) {side_label} variant#{i}: "
                    f"len={len(validated.result.text)} conf={q.get('confidence', 0)} garble={garble_score} "
                    f"len_delta={details.get('len_delta', 0.0):.2f} peer_sim={details.get('peer_sim', 0.0):.2f} "
                    f"score={score:.1f}"
                )
                log_event(
                    "OCR_CANDIDATE_SCORE",
                    "ocr candidate scored",
                    trace_id=self._trace_id,
                    side=side_label,
                    variant=int(i),
                    engine=str(engine_name),
                    mode=str(mode_for_log),
                    route=self._ocr_route_mode(),
                    score=float(score),
                    confidence=float(q.get("confidence", 0)),
                    garble_score=float(garble_score),
                    len_delta=float(details.get("len_delta", 0.0)),
                    peer_sim=float(details.get("peer_sim", 0.0)),
                    weird_tokens=float(details.get("weird_tokens", 0.0)),
                    duplicate_ratio=float(details.get("duplicate_ratio", 0.0)),
                    validation_penalty=float(validated.status.penalty),
                    validation_issues=list(validated.status.codes),
                )

                if score > best_score and validated.result.text:
                    best_score = score
                    best_result = validated.result
                    best_clip_bbox = last_clip_bbox
                elif abs(score - best_score) < 0.01 and validated.result.text and best_result is not None:
                    # Tie-break: prefer closer length to reference text.
                    ref_len = len((baseline_text or "").strip())
                    if ref_len > 0:
                        curr_delta = abs(len(validated.result.text) - ref_len)
                        best_delta = abs(len(best_result.text) - ref_len)
                        if curr_delta < best_delta:
                            best_result = validated.result
                            best_score = score
                            best_clip_bbox = last_clip_bbox

                if q.get("quality") == "good" and garble_score == 0 and len(validated.result.text) >= 20:
                    best_result = validated.result
                    best_score = score
                    best_clip_bbox = last_clip_bbox
                    break

            if best_result is not None:
                q_best = self._check_text_quality(best_result.text)
                garble_best, _ = self._garble_signal_score(best_result.text)
                min_accept_score = float(os.getenv("VERBATIM_OCR_MIN_ACCEPT_SCORE", "45") or "45")
                reference_accept_score = float(os.getenv("VERBATIM_OCR_REFERENCE_MIN_SCORE", "-55") or "-55")
                ref_len = len((baseline_text or "").strip())
                if ref_len <= 5:
                    min_accept_score = min(min_accept_score, 5.0)
                len_ratio = (len(best_result.text) / ref_len) if ref_len > 0 else 1.0
                if best_score < min_accept_score:
                    if (
                        ref_len <= 5
                        and best_result.text
                        and bool(os.getenv("VERBATIM_OCR_ACCEPT_LOW_SCORE_IF_EMPTY", "1") or "1")
                    ):
                        print(
                            f"[verbatim] OCR best {side_label} accepted as text-only reference "
                            f"(baseline empty): score={best_score:.1f}"
                        )
                        self._store_ocr_cache_result(
                            cache_key=effective_cache_key,
                            pdf_path=pdf_path,
                            page_number=page_number,
                            result=best_result,
                            clip_bbox=best_clip_bbox or last_clip_bbox or bbox,
                            source_bbox=cast(BBox, tuple(float(v) for v in bbox)),
                            coords_reliable=False,
                        )
                        return best_result.text
                    if best_score >= reference_accept_score and best_result.text and len_ratio <= 2.5:
                        print(
                            f"[verbatim] OCR best {side_label} accepted as text-only reference: "
                            f"score={best_score:.1f}"
                        )
                        self._store_ocr_cache_result(
                            cache_key=effective_cache_key,
                            pdf_path=pdf_path,
                            page_number=page_number,
                            result=best_result,
                            clip_bbox=best_clip_bbox or last_clip_bbox or bbox,
                            source_bbox=cast(BBox, tuple(float(v) for v in bbox)),
                            coords_reliable=False,
                        )
                        return best_result.text
                    print(f"[verbatim] OCR best {side_label} rejected: score={best_score:.1f} < {min_accept_score:.1f}")
                    log_event(
                        "OCR_BEST_REJECTED",
                        "ocr best candidate rejected",
                        trace_id=self._trace_id,
                        side=side_label,
                        reason="score_below_threshold",
                        score=float(best_score),
                        min_score=float(min_accept_score),
                    )
                elif q_best.get("quality") == "bad" and garble_best >= 2:
                    print(f"[verbatim] OCR best {side_label} rejected: quality=bad garble={garble_best}")
                    log_event(
                        "OCR_BEST_REJECTED",
                        "ocr best candidate rejected",
                        trace_id=self._trace_id,
                        side=side_label,
                        reason="quality_bad_garble",
                        score=float(best_score),
                        garble_score=float(garble_best),
                    )
                elif ref_len > 0 and len_ratio > 2.5 and best_score < 90.0:
                    print(f"[verbatim] OCR best {side_label} rejected: len_ratio={len_ratio:.2f} too large")
                    log_event(
                        "OCR_BEST_REJECTED",
                        "ocr best candidate rejected",
                        trace_id=self._trace_id,
                        side=side_label,
                        reason="len_ratio_too_large",
                        score=float(best_score),
                        len_ratio=float(len_ratio),
                    )
                else:
                    print(f"[verbatim] OCR best {side_label}: {len(best_result.text)} chars, score={best_score:.1f}")
                    self._store_ocr_cache_result(
                        cache_key=effective_cache_key,
                        pdf_path=pdf_path,
                        page_number=page_number,
                        result=best_result,
                        clip_bbox=best_clip_bbox or last_clip_bbox or bbox,
                        source_bbox=cast(BBox, tuple(float(v) for v in bbox)),
                        coords_reliable=bool(best_result.spans),
                    )
                    log_event(
                        "OCR_BEST_ACCEPTED",
                        "ocr best candidate accepted",
                        trace_id=self._trace_id,
                        side=side_label,
                        score=float(best_score),
                        length=int(len(best_result.text)),
                    )
                    validated = validate_ocr_result(
                        best_result,
                        baseline_text=baseline_text,
                        peer_text=peer_text,
                    )
                    span_issues = validate_ocr_spans(spans=best_result.spans, clip_bbox=last_clip_bbox)
                    if validated.status.codes or span_issues:
                        log_event(
                            "OCR_OUTPUT_VALIDATION",
                            "ocr output validation issues",
                            level="warning",
                            trace_id=self._trace_id,
                            side=side_label,
                            output_issues=list(validated.status.codes),
                            span_issues=span_issues,
                        )
                    return best_result.text

            if not _expanded_retry and not cloud_empty_file_seen and not cloud_timeout_seen:
                expanded_bbox = self._expand_bbox_for_ocr(bbox)
                print(f"[verbatim] OCR({side_label}) retry with expanded bbox once")
                log_event(
                    "OCR_RETRY_EXPANDED_BBOX",
                    "ocr retry with expanded bbox",
                    trace_id=self._trace_id,
                    side=side_label,
                )
                return self._try_ocr_text(
                    pdf_path,
                    page_number,
                    expanded_bbox,
                    side_label,
                    baseline_text=baseline_text,
                    peer_text=peer_text,
                    _expanded_retry=True,
                    _origin_cache_key=effective_cache_key,
                )
            if cloud_empty_file_seen:
                print(f"[verbatim] OCR({side_label}) skip expanded retry: cloud returned empty-file")
                log_event(
                    "OCR_RETRY_SKIPPED",
                    "ocr retry skipped due to empty file",
                    trace_id=self._trace_id,
                    side=side_label,
                    reason="empty_file",
                )
            if cloud_timeout_seen:
                print(f"[verbatim] OCR({side_label}) skip expanded retry: cloud timed out")
                log_event(
                    "OCR_RETRY_SKIPPED",
                    "ocr retry skipped due to timeout",
                    trace_id=self._trace_id,
                    side=side_label,
                    reason="timeout",
                )
            empty_spans = tuple(self._ocr_result_spans_cache.get(effective_cache_key, []))
            empty_result = OcrResult(
                text="",
                raw_text="",
                spans=empty_spans,
            )
            validated_empty = validate_ocr_result(
                empty_result,
                baseline_text=baseline_text,
                peer_text=peer_text,
            )
            span_issues = validate_ocr_spans(spans=empty_result.spans, clip_bbox=last_clip_bbox)
            image_stub = b"x" * min(max(last_image_len, 0), 64)
            log_event(
                "OCR_EMPTY_RESULT",
                "ocr returned empty result",
                level="warning",
                trace_id=self._trace_id,
                side=side_label,
                route=self._ocr_route_mode(),
                mode=self._current_ocr_mode(),
                bbox=[float(x) for x in bbox],
                clip_bbox=[float(x) for x in last_clip_bbox] if last_clip_bbox else [],
                image_bytes=int(last_image_len),
                zoom=float(last_zoom or 0.0),
                input_issues=validate_ocr_input(
                    pdf_path=pdf_path,
                    page_number=page_number,
                    bbox=bbox,
                    image_bytes=image_stub,
                    clip_bbox=last_clip_bbox,
                    zoom=float(last_zoom or 0.0),
                ),
                output_issues=list(validated_empty.status.codes),
                span_issues=span_issues,
            )
            self._record_ocr_error(side_label, "empty OCR result", code_override="empty_result")
            return ""
        except Exception as e:
            print(f"[verbatim] OCR failed on {side_label}: {e}")
            self._record_ocr_error(side_label, e)
            log_event(
                "OCR_FAIL",
                "ocr failed",
                level="error",
                trace_id=self._trace_id,
                side=side_label,
                route=self._ocr_route_mode(),
                mode=self._current_ocr_mode(),
                error=str(e),
            )
            return ""

    def _extract_regions_with_mode(self, mode: str):
        """Extract left/right regions using a specific reading-order mode."""
        left_region = extract_region(
            self._left_page,  # type: ignore[arg-type]
            [self._left_sel_bbox],  # type: ignore[list-item]
            strict_bounds=True,
            reading_order_mode=mode,
        )
        right_region = extract_region(
            self._right_page,  # type: ignore[arg-type]
            [self._right_sel_bbox],  # type: ignore[list-item]
            strict_bounds=True,
            reading_order_mode=mode,
        )
        return left_region, right_region

    def _choose_best_auto_mode(self) -> tuple[str, RegionData, RegionData]:
        """Try multiple reading-order modes and pick the one with best combined score."""
        from core.models import DiffOpType

        candidates = ["auto", "raw", "single_column", "two_column"]
        best_mode = "auto"
        left0, right0 = self._extract_regions_with_mode("auto")
        best_pair = (left0, right0)
        best_score = (10**9, 10**9, 10**9)

        for mode in candidates:
            left_region, right_region = self._extract_regions_with_mode(mode)
            left_text = "".join(ch.char for ch in left_region.chars)
            right_text = "".join(ch.char for ch in right_region.chars)
            left_quality = self._check_text_quality(left_text)
            right_quality = self._check_text_quality(right_text)
            left_quality = self._adjust_quality_with_doc(
                left_quality, force_ocr=bool(self._left_force_ocr), text=left_text
            )
            right_quality = self._adjust_quality_with_doc(
                right_quality, force_ocr=bool(self._right_force_ocr), text=right_text
            )

            severity_score = (
                (100 if left_quality.get("quality") == "bad" else 20 if left_quality.get("quality") == "warning" else 0)
                + (
                    100
                    if right_quality.get("quality") == "bad"
                    else 20
                    if right_quality.get("quality") == "warning"
                    else 0
                )
                + len(left_quality.get("issues", []))
                + len(right_quality.get("issues", []))
            )

            ops, _ = diff_regions(
                left_region,
                right_region,
                pure_content_mode=True,
                ignore_punctuation=True,
                normalize_numbers=True,
                merge_key_value_lines=True,
            )
            text_ops = [o for o in ops if o.type != DiffOpType.FORMAT_CHANGE]
            op_count = len(text_ops)
            char_count = sum(len(o.left_indices) + len(o.right_indices) for o in text_ops)
            score = (severity_score, op_count, char_count)
            if score < best_score:
                best_score = score
                best_mode = mode
                best_pair = (left_region, right_region)

        return best_mode, best_pair[0], best_pair[1]

    def _check_text_quality(self, text: str) -> dict:
        return svc_check_text_quality(text)

    def _assess_pdf_side_quality(self, pdf_path: Path, page_count: int, side: str) -> tuple[bool, str]:
        return self._run_process_task_with_ui_pump(
            "assess_pdf_side_quality",
            str(pdf_path),
            int(page_count),
            str(side),
            timeout_ms=max(int(self._bg_task_timeout_ms), 10000),
        )

    def _normalized_similarity(self, left_text: str, right_text: str) -> float:
        return svc_normalized_similarity(left_text, right_text)

    @staticmethod
    def _normalize_for_span_check(text: str) -> str:
        t = (text or "").lower()
        t = re.sub(r"\s+", "", t)
        t = re.sub(r"[，。；：！？、,.!?;:'\"()\[\]【】“”‘’\-—·]", "", t)
        return t

    def _spans_match_text(self, spans: list[OcrSpan], text: str) -> bool:
        reconstructed = "".join((s.text or "") for s in spans)
        return self._normalize_for_span_check(reconstructed) == self._normalize_for_span_check(text)

    @staticmethod
    def _median(values: list[float]) -> float:
        if not values:
            return 0.0
        vals = sorted(values)
        mid = len(vals) // 2
        if len(vals) % 2 == 1:
            return float(vals[mid])
        return float((vals[mid - 1] + vals[mid]) / 2.0)

    def _check_spans_geometry(self, spans: list[OcrSpan]) -> tuple[bool, list[str]]:
        if not spans or len(spans) < 2:
            return True, []

        heights = []
        for s in spans:
            x0, y0, x1, y1 = s.bbox
            heights.append(max(0.1, float(y1 - y0)))
        median_h = self._median(heights)
        tol = max(1.0, median_h * 0.6)

        spans_sorted = sorted(spans, key=lambda s: ((s.bbox[1] + s.bbox[3]) / 2.0, s.bbox[0]))
        rows: list[dict] = []
        for s in spans_sorted:
            cy = (s.bbox[1] + s.bbox[3]) / 2.0
            placed = False
            for row in rows:
                if abs(cy - row["cy"]) <= tol:
                    row["spans"].append(s)
                    row["cy"] = (row["cy"] * row["n"] + cy) / (row["n"] + 1)
                    row["n"] += 1
                    placed = True
                    break
            if not placed:
                rows.append({"cy": cy, "spans": [s], "n": 1})

        reasons: list[str] = []

        # y 行分组稳定性：每行的垂直跨度不应过大
        for row in rows:
            ys = [float(sp.bbox[1]) for sp in row["spans"]] + [float(sp.bbox[3]) for sp in row["spans"]]
            if not ys:
                continue
            spread = max(ys) - min(ys)
            if spread > max(8.0, median_h * 2.5):
                reasons.append("row_y_spread_too_large")
                break

        # x 方向单调性（同一行）
        for row in rows:
            ids = {id(sp) for sp in row["spans"]}
            row_in_order = [sp for sp in spans if id(sp) in ids]
            if len(row_in_order) < 2:
                continue
            x0s = [float(sp.bbox[0]) for sp in row_in_order]
            inversions = sum(1 for i in range(1, len(x0s)) if x0s[i] + 0.5 < x0s[i - 1])
            if inversions > max(1, int(len(x0s) * 0.1)):
                reasons.append("row_x_not_monotonic")
                break

        # bbox 重叠过多（同一行）
        for row in rows:
            row_spans = sorted(row["spans"], key=lambda s: s.bbox[0])
            if len(row_spans) < 2:
                continue
            overlap_hits = 0
            pairs = 0
            for i in range(1, len(row_spans)):
                a = row_spans[i - 1].bbox
                b = row_spans[i].bbox
                ax0, ay0, ax1, ay1 = [float(v) for v in a]
                bx0, by0, bx1, by1 = [float(v) for v in b]
                overlap = max(0.0, min(ax1, bx1) - max(ax0, bx0))
                min_w = max(1.0, min(ax1 - ax0, bx1 - bx0))
                if overlap / min_w > 0.5:
                    overlap_hits += 1
                pairs += 1
            if pairs and overlap_hits / pairs > 0.2:
                reasons.append("row_bbox_overlap_high")
                break

        return (len(reasons) == 0), reasons

    def _log_spans_check(self, side: str, spans: list[OcrSpan], text_ok: bool, geom_ok: bool, reasons: list[str]) -> None:
        span_count = len(spans or [])
        reason_txt = ",".join(reasons) if reasons else "ok"
        print(
            f"[SPANS_CHECK] side={side} spans_count={span_count} text_ok={text_ok} geom_ok={geom_ok} reasons={reason_txt}"
        )
        log_event(
            "OCR_SPANS_CHECK",
            "ocr spans check",
            trace_id=self._trace_id,
            side=side,
            spans=span_count,
            text_ok=bool(text_ok),
            geom_ok=bool(geom_ok),
            reasons=list(reasons or []),
        )

    def _garble_signal_score(self, text: str) -> tuple[int, list[str]]:
        return svc_garble_signal_score(text)

    def _should_try_ocr_side(self, text: str, quality: dict) -> tuple[bool, str]:
        return svc_should_try_ocr_side(text, quality)

    def _filter_low_confidence_noise_ops(
        self, ops, left_text: str, right_text: str, left_quality: dict, right_quality: dict
    ):
        """Suppress tiny noisy content ops under low confidence text layers."""
        from core.models import DiffOpType

        left_q = str(left_quality.get("quality", "good"))
        right_q = str(right_quality.get("quality", "good"))
        if left_q == "good" and right_q == "good":
            return ops, 0

        sim = self._normalized_similarity(left_text, right_text)
        if sim < 0.90:
            return ops, 0

        filtered = []
        removed = 0
        for op in ops:
            if op.type == DiffOpType.FORMAT_CHANGE:
                filtered.append(op)
                continue

            left_seg = str(op.meta.get("left_text", ""))
            right_seg = str(op.meta.get("right_text", ""))
            seg_len = max(len(left_seg), len(right_seg))
            if self._is_field_token(left_seg) or self._is_field_token(right_seg):
                filtered.append(op)
                continue

            # For highly similar text regions, tiny char-level mismatches are usually text-layer noise.
            if (sim >= 0.94 and seg_len <= 2) or (sim >= 0.98 and seg_len <= 3):
                removed += 1
                continue
            filtered.append(op)

        return filtered, removed

    @staticmethod
    def _is_field_token(text: str) -> bool:
        t = str(text or "").strip()
        if not t:
            return False
        if re.search(r"(名称|企业|公司|地址|电话|联系人|批准|批件|规格|成分|有效期|生产企业)", t):
            return True
        if len(t) == 1:
            key_chars = set("名称企业公司地址电话联系人批准批件规格成分有效期生产")
            return t in key_chars
        return False

    def _is_weak_confusable_pair(self, left_seg: str, right_seg: str) -> bool:
        return svc_is_weak_confusable_pair(left_seg, right_seg)

    def _suppress_weak_confusable_ops(
        self,
        ops,
        left_text: str,
        right_text: str,
        *,
        ocr_used: bool,
        left_quality: dict,
        right_quality: dict,
    ):
        from core.models import DiffOpType

        left_q = str(left_quality.get("quality", "good"))
        right_q = str(right_quality.get("quality", "good"))
        sim = self._normalized_similarity(left_text, right_text)
        if not ocr_used and left_q == "good" and right_q == "good":
            return ops, 0
        if sim < 0.93:
            return ops, 0

        kept = []
        removed = 0
        for op in ops:
            if op.type != DiffOpType.REPLACE:
                kept.append(op)
                continue
            left_seg = str(op.meta.get("left_text", ""))
            right_seg = str(op.meta.get("right_text", ""))
            if self._is_field_token(left_seg) or self._is_field_token(right_seg):
                kept.append(op)
                continue
            if self._is_weak_confusable_pair(left_seg, right_seg):
                removed += 1
                continue
            kept.append(op)
        return kept, removed

    @staticmethod
    def _quality_severity(q: dict) -> int:
        if str(q.get("quality", "good")) == "bad":
            base = 100
        elif str(q.get("quality", "good")) == "warning":
            base = 20
        else:
            base = 0
        return base + len(q.get("issues", []))

    def _adjust_quality_with_doc(self, quality: dict, *, force_ocr: bool, text: str) -> dict:
        if force_ocr:
            return quality
        q = dict(quality or {})
        if str(q.get("quality", "good")) == "warning":
            garble_score, _ = self._garble_signal_score(text)
            char_count = int(q.get("char_count", 0) or 0)
            if garble_score == 0 and char_count >= 40:
                q["quality"] = "good"
                q["issues"] = [i for i in q.get("issues", []) if "标点密度" not in str(i)]
        return q

    def _on_compare_clicked(self) -> None:
        """Compare only the two user-selected regions (stable manual alignment model)."""
        if self._is_comparing:
            return

        if self._left_page is None or self._right_page is None:
            print("[verbatim] Page data not ready yet; please select PDFs first.")
            return
        if self._left_sel_bbox is None or self._right_sel_bbox is None:
            print("[verbatim] Please select both left and right regions before Compare.")
            return
        self._begin_compare_feedback()

        try:
            selected_mode = self._current_reading_order_mode()
            if selected_mode == "auto":
                mode_used, left_region, right_region = self._choose_best_auto_mode()
            else:
                mode_used = selected_mode
                left_region, right_region = self._extract_regions_with_mode(mode_used)
            left_coords_reliable = True
            right_coords_reliable = True
            print(f"[verbatim] Reading-order mode used: {mode_used}")
            log_event(
                "READING_ORDER_USED",
                "reading order mode selected",
                trace_id=self._trace_id,
                mode=str(mode_used),
            )

            # Debug: Print extracted text for troubleshooting
            left_text = "".join(ch.char for ch in left_region.chars)
            right_text = "".join(ch.char for ch in right_region.chars)
            print(f"[verbatim] DEBUG: Left region ({len(left_region.chars)} chars):")
            self._log_text_preview("DEBUG Left preview", left_text, limit=200)
            print(f"[verbatim] DEBUG: Right region ({len(right_region.chars)} chars):")
            self._log_text_preview("DEBUG Right preview", right_text, limit=200)

            # ============================================================
            # v1.0 PDF text-layer quality assessment (P0)
            # ============================================================
            left_quality = self._check_text_quality(left_text)
            right_quality = self._check_text_quality(right_text)
            extra_quality_notes: list[str] = []

            warnings, quality_scores = collect_quality_warnings(
                left_doc_note=self._left_doc_quality_note,
                right_doc_note=self._right_doc_quality_note,
                left_quality=left_quality,
                right_quality=right_quality,
            )

            if warnings:
                warning_text = "\\n".join(warnings)
                print("[verbatim] WARNING: PDF text layer quality issues detected!")
                print(f"  {warning_text}")
                log_event(
                    "CMP_QUALITY_WARN",
                    "text layer quality warnings detected",
                    level="warning",
                    trace_id=self._trace_id,
                    warning_count=len(warnings),
                )
                # Store warnings for display
                self._last_quality_warnings = [*warnings, *extra_quality_notes]
            else:
                self._last_quality_warnings = [*extra_quality_notes]
            self._last_quality_scores = quality_scores

            ocr_used = False
            self._last_ocr_note = ""
            self._last_ocr_errors = []
            self._last_ocr_error_codes = []
            left_ocr_applied = False
            right_ocr_applied = False
            use_ocr = bool(self._auto_ocr_enabled and self._btn_use_ocr.isChecked())
            ocr_decision = decide_ocr(
                left_text=left_text,
                right_text=right_text,
                left_quality=left_quality,
                right_quality=right_quality,
                left_force_ocr=bool(self._left_force_ocr),
                right_force_ocr=bool(self._right_force_ocr),
                dual_ocr_linkage=bool(self._btn_dual_ocr.isChecked()),
                should_try_ocr_side=self._should_try_ocr_side,
            )
            left_try_ocr = ocr_decision.left_try_ocr
            right_try_ocr = ocr_decision.right_try_ocr
            left_ocr_reason = ocr_decision.left_reason
            right_ocr_reason = ocr_decision.right_reason
            print(
                f"[verbatim] OCR decision: left={left_try_ocr} ({left_ocr_reason}); right={right_try_ocr} ({right_ocr_reason})"
            )
            log_event(
                "OCR_DECISION",
                "ocr decision computed",
                trace_id=self._trace_id,
                route=self._ocr_route_mode(),
                mode=self._current_ocr_mode(),
                left_try_ocr=bool(left_try_ocr),
                right_try_ocr=bool(right_try_ocr),
            )
            ocr_recommended = ocr_decision.recommended
            if ocr_recommended and self._ocr_cfg is None and self._ocr_route_mode() == "cloud_only":
                warn_note = "未配置云端OCR Token：当前云端模式不可用，已降级继续比对。"
                self._last_quality_warnings = [*self._last_quality_warnings, warn_note]
                self._set_result_summary("低精度模式：未配置云端 OCR Token（云端模式）", warn=True)

            can_run_ocr = (
                self._left_pdf is not None
                and self._right_pdf is not None
                and self._left_sel_bbox is not None
                and self._right_sel_bbox is not None
            )
            ocr_was_recommended = bool(left_try_ocr or right_try_ocr)
            local_check = self._get_local_ocr_self_check()
            if (
                self._ocr_route_mode() in {"local_first", "local_only"}
                and local_check is not None
                and not local_check.available
                and ocr_was_recommended
            ):
                local_runtime_note = (
                    f"本地 OCR 不可用（{local_check.code}: {local_check.message}），"
                    + ("已自动切云端" if self._ocr_route_mode() == "local_first" else "当前仅允许本地OCR")
                )
                self._last_quality_warnings = [*self._last_quality_warnings, local_runtime_note]
            has_ocr_config = bool(self._resolve_ocr_engines())
            ocr_result = run_ocr_fallback(
                use_ocr=bool(use_ocr and can_run_ocr),
                has_ocr_config=bool(has_ocr_config),
                left_try_ocr=left_try_ocr,
                right_try_ocr=right_try_ocr,
                left_text=left_text,
                right_text=right_text,
                fetch_ocr_text=lambda side, baseline, peer: self._try_ocr_text(
                    self._left_pdf if side == "left" else self._right_pdf,  # type: ignore[arg-type]
                    self._left_page_number if side == "left" else self._right_page_number,
                    self._left_sel_bbox if side == "left" else self._right_sel_bbox,  # type: ignore[arg-type]
                    side,
                    baseline_text=baseline,
                    peer_text=peer,
                ),
            )
            left_text = ocr_result.left_text
            right_text = ocr_result.right_text
            left_ocr_applied = ocr_result.left_ocr_applied
            right_ocr_applied = ocr_result.right_ocr_applied
            ocr_used = ocr_result.ocr_used
            ocr_status = compute_ocr_status(
                use_ocr=bool(use_ocr),
                can_run_ocr=bool(can_run_ocr),
                has_ocr_config=bool(has_ocr_config),
                ocr_was_recommended=bool(ocr_was_recommended),
                replaced_sides=list(ocr_result.replaced_sides or []),
                attempted_but_empty=bool(ocr_result.attempted_but_empty),
            )
            self._last_ocr_state = ocr_status.state
            self._last_ocr_state_reason = ocr_status.reason
            log_event(
                "OCR_STATE",
                "ocr state computed",
                trace_id=self._trace_id,
                state=str(ocr_status.state),
                reason=str(ocr_status.reason),
                detail=str(ocr_status.detail or ""),
            )
            left_ocr_has_coords = False
            right_ocr_has_coords = False
            if ocr_result.replaced_sides:
                if left_ocr_applied:
                    left_key = self._ocr_cache_key(
                        self._left_pdf,  # type: ignore[arg-type]
                        self._left_page_number,
                        self._left_sel_bbox,  # type: ignore[arg-type]
                        "left",
                    )
                    left_spans = self._get_cached_ocr_spans(left_key)
                    left_cached_coords_reliable = self._ocr_cached_coords_reliable(left_key)
                    self._last_left_spans = list(left_spans or [])
                    left_meta = self._ocr_result_spans_meta.get(left_key)
                    if left_spans:
                        self._last_left_spans_meta = left_meta or {
                            'pdf': str(self._left_pdf),
                            'page': int(self._left_page_number),
                            'clip_bbox': list(self._left_sel_bbox or []),
                            'source_bbox': list(self._left_sel_bbox or []),
                        }
                    else:
                        self._last_left_spans_meta = None
                    span_issues = validate_ocr_spans(spans=left_spans, clip_bbox=self._left_sel_bbox)
                    text_ok = bool(left_spans) and self._spans_match_text(left_spans, left_text)
                    geom_ok, geom_reasons = (True, [])
                    if left_spans and not span_issues:
                        geom_ok, geom_reasons = self._check_spans_geometry(left_spans)
                    if left_spans:
                        reasons = list(span_issues or [])
                        if not text_ok:
                            reasons = ["text_mismatch"]
                        if not geom_ok:
                            reasons = ["geometry_mismatch"]
                        self._log_spans_check("left", left_spans, text_ok, geom_ok, reasons)
                    else:
                        self._log_spans_check("left", [], False, False, ["no_spans"])
                        self._last_quality_warnings = [
                            *self._last_quality_warnings,
                            "OCR spans 不存在（左侧），定位标注已禁用。",
                        ]
                    if left_spans and not span_issues and not geom_ok:
                        span_issues = ["span_geometry_mismatch"]
                        self._last_quality_warnings = [
                            *self._last_quality_warnings,
                            "OCR spans 几何一致性校验失败（左侧），定位标注已禁用。",
                        ]
                    if left_spans and not span_issues and not text_ok:
                        self._last_quality_warnings = [
                            *self._last_quality_warnings,
                            "标注基于 OCR，文本存在差异，可能不完全准确（左侧）。",
                        ]
                    if left_spans and not span_issues and left_cached_coords_reliable:
                        left_region = self._build_region_from_ocr_spans(left_spans, self._left_page_number)
                        left_coords_reliable = True
                        left_ocr_has_coords = True
                    elif left_spans and span_issues:
                        self._append_spans_unlocatable_warning("left", "span_validation_failed")
                        log_event(
                            "OCR_SPANS_INVALID",
                            "ocr spans invalid for left",
                            level="warning",
                            trace_id=self._trace_id,
                            side="left",
                            issues=span_issues,
                        )
                        left_region = self._build_region_from_text(left_text, self._left_page_number)
                        left_coords_reliable = False
                    else:
                        left_region = self._build_region_from_text(left_text, self._left_page_number)
                        left_coords_reliable = False
                if right_ocr_applied:
                    right_key = self._ocr_cache_key(
                        self._right_pdf,  # type: ignore[arg-type]
                        self._right_page_number,
                        self._right_sel_bbox,  # type: ignore[arg-type]
                        "right",
                    )
                    right_spans = self._get_cached_ocr_spans(right_key)
                    right_cached_coords_reliable = self._ocr_cached_coords_reliable(right_key)
                    self._last_right_spans = list(right_spans or [])
                    right_meta = self._ocr_result_spans_meta.get(right_key)
                    if right_spans:
                        self._last_right_spans_meta = right_meta or {
                            'pdf': str(self._right_pdf),
                            'page': int(self._right_page_number),
                            'clip_bbox': list(self._right_sel_bbox or []),
                            'source_bbox': list(self._right_sel_bbox or []),
                        }
                    else:
                        self._last_right_spans_meta = None
                    span_issues = validate_ocr_spans(spans=right_spans, clip_bbox=self._right_sel_bbox)
                    text_ok = bool(right_spans) and self._spans_match_text(right_spans, right_text)
                    geom_ok, geom_reasons = (True, [])
                    if right_spans and not span_issues:
                        geom_ok, geom_reasons = self._check_spans_geometry(right_spans)
                    if right_spans:
                        reasons = list(span_issues or [])
                        if not text_ok:
                            reasons = ["text_mismatch"]
                        if not geom_ok:
                            reasons = ["geometry_mismatch"]
                        self._log_spans_check("right", right_spans, text_ok, geom_ok, reasons)
                    else:
                        self._log_spans_check("right", [], False, False, ["no_spans"])
                        self._last_quality_warnings = [
                            *self._last_quality_warnings,
                            "OCR spans 不存在（右侧），定位标注已禁用。",
                        ]
                    if right_spans and not span_issues and not geom_ok:
                        span_issues = ["span_geometry_mismatch"]
                        self._last_quality_warnings = [
                            *self._last_quality_warnings,
                            "OCR spans 几何一致性校验失败（右侧），定位标注已禁用。",
                        ]
                    if right_spans and not span_issues and not text_ok:
                        self._last_quality_warnings = [
                            *self._last_quality_warnings,
                            "标注基于 OCR，文本存在差异，可能不完全准确（右侧）。",
                        ]
                    if right_spans and not span_issues and right_cached_coords_reliable:
                        right_region = self._build_region_from_ocr_spans(right_spans, self._right_page_number)
                        right_coords_reliable = True
                        right_ocr_has_coords = True
                    elif right_spans and span_issues:
                        self._append_spans_unlocatable_warning("right", "span_validation_failed")
                        log_event(
                            "OCR_SPANS_INVALID",
                            "ocr spans invalid for right",
                            level="warning",
                            trace_id=self._trace_id,
                            side="right",
                            issues=span_issues,
                        )
                        right_region = self._build_region_from_text(right_text, self._right_page_number)
                        right_coords_reliable = False
                    else:
                        right_region = self._build_region_from_text(right_text, self._right_page_number)
                        right_coords_reliable = False
                left_quality = self._check_text_quality(left_text)
                right_quality = self._check_text_quality(right_text)
                self._last_ocr_note = ocr_result.ocr_note
                self._last_quality_warnings = [*self._last_quality_warnings, self._last_ocr_note]
                print(f"[verbatim] OCR fallback enabled for: {', '.join(ocr_result.replaced_sides)}")
                log_event(
                    "OCR_FALLBACK_APPLIED",
                    "ocr fallback applied",
                    trace_id=self._trace_id,
                    route=self._ocr_route_mode(),
                    mode=self._current_ocr_mode(),
                    left_ocr_applied=bool(left_ocr_applied),
                    right_ocr_applied=bool(right_ocr_applied),
                )
                self._last_left_ocr_has_coords = bool(left_ocr_has_coords)
                self._last_right_ocr_has_coords = bool(right_ocr_has_coords)
            elif ocr_result.attempted_but_empty:
                print("[verbatim] OCR attempted but no valid OCR text returned.")
                empty_note = "OCR已尝试但未返回有效文本，已回退到文本层结果。"
                self._last_quality_warnings = [*self._last_quality_warnings, empty_note]
                if self._last_ocr_errors:
                    ocr_err_note = f"OCR请求失败：{' | '.join(self._last_ocr_errors)}"
                    self._last_quality_warnings = [*self._last_quality_warnings, ocr_err_note]
                if self._last_ocr_error_codes:
                    code_note = f"OCR失败类型：{', '.join(sorted(set(self._last_ocr_error_codes)))}"
                    self._last_quality_warnings = [*self._last_quality_warnings, code_note]
            elif ocr_result.skipped_no_config and use_ocr:
                print("[verbatim] OCR skipped: no OCR token/config found.")
                log_event(
                    "OCR_SKIPPED_NO_CONFIG",
                    "ocr skipped due to missing config",
                    level="warning",
                    trace_id=self._trace_id,
                )
                if ocr_was_recommended:
                    self._last_quality_warnings = [
                        *self._last_quality_warnings,
                        "OCR未触发：未检测到可用配置/Token。",
                    ]
            elif ocr_was_recommended and not use_ocr:
                self._last_quality_warnings = [
                    *self._last_quality_warnings,
                    "OCR未触发：自动OCR已关闭。",
                ]
            if not left_ocr_applied:
                self._log_spans_check("left", [], False, False, ["ocr_not_applied"])
            if not right_ocr_applied:
                self._log_spans_check("right", [], False, False, ["ocr_not_applied"])
            if not ocr_result.replaced_sides:
                self._last_left_ocr_has_coords = False
                self._last_right_ocr_has_coords = False

            if self._last_ocr_state == OcrRunState.FAILURE:
                self._last_quality_warnings = [
                    *self._last_quality_warnings,
                    f"OCR状态：失败（原因={self._last_ocr_state_reason}）",
                ]
            elif self._last_ocr_state == OcrRunState.PARTIAL:
                self._last_quality_warnings = [
                    *self._last_quality_warnings,
                    f"OCR状态：部分成功（原因={self._last_ocr_state_reason}）",
                ]
            elif self._last_ocr_state == OcrRunState.BLOCKED and ocr_was_recommended:
                self._last_quality_warnings = [
                    *self._last_quality_warnings,
                    f"OCR状态：阻塞（原因={self._last_ocr_state_reason}）",
                ]

            compare_status, compare_status_note = self._compare_decision_status(
                left_text=left_text,
                right_text=right_text,
                left_quality=left_quality,
                right_quality=right_quality,
                ocr_used=bool(ocr_used),
                left_ocr_applied=bool(left_ocr_applied),
                right_ocr_applied=bool(right_ocr_applied),
                ocr_errors=list(self._last_ocr_errors),
                ocr_was_recommended=bool(ocr_was_recommended),
                ocr_state=self._last_ocr_state,
                ocr_state_reason=str(self._last_ocr_state_reason),
                ocr_error_codes=list(self._last_ocr_error_codes),
                left_coords_reliable=bool(left_coords_reliable),
                right_coords_reliable=bool(right_coords_reliable),
            )
            self._last_compare_decision_status = compare_status
            self._last_gate_reason = str(compare_status_note or "")
            self._last_fallback_reason = ""
            self._last_decision_basis = self._decision_basis_for_compare(
                ocr_used=bool(ocr_used),
                visual_diff_used=False,
            )
            if compare_status_note:
                self._last_quality_warnings = [*self._last_quality_warnings, compare_status_note]
            if ocr_result.attempted_but_empty and bool(left_try_ocr or right_try_ocr):
                compare_status = "REVIEW"
                compare_status_note = "OCR尝试失败且未返回有效文本，已降级为人工复核"
                self._last_compare_decision_status = compare_status
                self._last_quality_warnings = [*self._last_quality_warnings, compare_status_note]

            manual_review_confirmed = False
            if compare_status == "REVIEW":
                original_left_text = left_text
                original_right_text = right_text
                approved, confirmed_left_text, confirmed_right_text = self._run_manual_review_gate(
                    reason=(compare_status_note or "OCR链路低可信"),
                    left_text=left_text,
                    right_text=right_text,
                )
                if not approved:
                    self._last_diff_ops = []
                    self._last_left_region = None
                    self._last_right_region = None
                    self._focused_diff_op = None
                    self._populate_diff_list([])
                    self._set_result_summary("已取消本次比对：低可信场景需人工确认后才能继续", warn=True)
                    return
                manual_review_confirmed = True
                left_text = confirmed_left_text
                right_text = confirmed_right_text
                left_text_modified = left_text != original_left_text
                right_text_modified = right_text != original_right_text

                left_region = None
                right_region = None
                left_coords_reliable = False
                right_coords_reliable = False

                if left_ocr_applied and not left_text_modified:
                    left_key = self._ocr_cache_key(
                        self._left_pdf,  # type: ignore[arg-type]
                        self._left_page_number,
                        self._left_sel_bbox,  # type: ignore[arg-type]
                        "left",
                    )
                    left_spans = self._get_cached_ocr_spans(left_key)
                    left_cached_coords_reliable = self._ocr_cached_coords_reliable(left_key)
                    self._last_left_spans = list(left_spans or [])
                    left_meta = self._ocr_result_spans_meta.get(left_key)
                    if left_spans:
                        self._last_left_spans_meta = left_meta or {
                            'pdf': str(self._left_pdf),
                            'page': int(self._left_page_number),
                            'clip_bbox': list(self._left_sel_bbox or []),
                            'source_bbox': list(self._left_sel_bbox or []),
                        }
                    else:
                        self._last_left_spans_meta = None
                    span_issues = validate_ocr_spans(spans=left_spans, clip_bbox=self._left_sel_bbox)
                    text_ok = bool(left_spans) and self._spans_match_text(left_spans, left_text)
                    geom_ok, geom_reasons = (True, [])
                    if left_spans and not span_issues:
                        geom_ok, geom_reasons = self._check_spans_geometry(left_spans)
                    if left_spans:
                        reasons = list(span_issues or [])
                        if not text_ok:
                            reasons = ["text_mismatch"]
                        if not geom_ok:
                            reasons = ["geometry_mismatch"]
                        self._log_spans_check("left", left_spans, text_ok, geom_ok, reasons)
                    else:
                        self._log_spans_check("left", [], False, False, ["no_spans"])
                        self._last_quality_warnings = [
                            *self._last_quality_warnings,
                            "OCR spans 不存在（左侧），定位标注已禁用。",
                        ]
                    if left_spans and not span_issues and not geom_ok:
                        span_issues = ["span_geometry_mismatch"]
                        self._last_quality_warnings = [
                            *self._last_quality_warnings,
                            "OCR spans 几何一致性校验失败（左侧），定位标注已禁用。",
                        ]
                    if left_spans and not span_issues and left_cached_coords_reliable:
                        if not text_ok:
                            self._last_quality_warnings = [
                                *self._last_quality_warnings,
                                "标注基于 OCR，文本存在差异，可能不完全准确（左侧）。",
                            ]
                        left_region = self._build_region_from_ocr_spans(left_spans, self._left_page_number)
                        left_coords_reliable = True
                        self._last_left_ocr_has_coords = True
                    else:
                        self._last_left_ocr_has_coords = False
                elif left_ocr_applied and left_text_modified:
                    self._last_quality_warnings = [
                        *self._last_quality_warnings,
                        "人工确认文本已修改（左侧），定位标注已禁用。",
                    ]

                if right_ocr_applied and not right_text_modified:
                    right_key = self._ocr_cache_key(
                        self._right_pdf,  # type: ignore[arg-type]
                        self._right_page_number,
                        self._right_sel_bbox,  # type: ignore[arg-type]
                        "right",
                    )
                    right_spans = self._get_cached_ocr_spans(right_key)
                    right_cached_coords_reliable = self._ocr_cached_coords_reliable(right_key)
                    self._last_right_spans = list(right_spans or [])
                    right_meta = self._ocr_result_spans_meta.get(right_key)
                    if right_spans:
                        self._last_right_spans_meta = right_meta or {
                            'pdf': str(self._right_pdf),
                            'page': int(self._right_page_number),
                            'clip_bbox': list(self._right_sel_bbox or []),
                            'source_bbox': list(self._right_sel_bbox or []),
                        }
                    else:
                        self._last_right_spans_meta = None
                    span_issues = validate_ocr_spans(spans=right_spans, clip_bbox=self._right_sel_bbox)
                    text_ok = bool(right_spans) and self._spans_match_text(right_spans, right_text)
                    geom_ok, geom_reasons = (True, [])
                    if right_spans and not span_issues:
                        geom_ok, geom_reasons = self._check_spans_geometry(right_spans)
                    if right_spans:
                        reasons = list(span_issues or [])
                        if not text_ok:
                            reasons = ["text_mismatch"]
                        if not geom_ok:
                            reasons = ["geometry_mismatch"]
                        self._log_spans_check("right", right_spans, text_ok, geom_ok, reasons)
                    else:
                        self._log_spans_check("right", [], False, False, ["no_spans"])
                        self._last_quality_warnings = [
                            *self._last_quality_warnings,
                            "OCR spans 不存在（右侧），定位标注已禁用。",
                        ]
                    if right_spans and not span_issues and not geom_ok:
                        span_issues = ["span_geometry_mismatch"]
                        self._last_quality_warnings = [
                            *self._last_quality_warnings,
                            "OCR spans 几何一致性校验失败（右侧），定位标注已禁用。",
                        ]
                    if right_spans and not span_issues and right_cached_coords_reliable:
                        if not text_ok:
                            self._last_quality_warnings = [
                                *self._last_quality_warnings,
                                "标注基于 OCR，文本存在差异，可能不完全准确（右侧）。",
                            ]
                        right_region = self._build_region_from_ocr_spans(right_spans, self._right_page_number)
                        right_coords_reliable = True
                        self._last_right_ocr_has_coords = True
                    else:
                        self._last_right_ocr_has_coords = False
                elif right_ocr_applied and right_text_modified:
                    self._last_quality_warnings = [
                        *self._last_quality_warnings,
                        "人工确认文本已修改（右侧），定位标注已禁用。",
                    ]

                if left_region is None:
                    left_region = self._build_region_from_text(left_text, self._left_page_number)
                if right_region is None:
                    right_region = self._build_region_from_text(right_text, self._right_page_number)
                left_quality = self._check_text_quality(left_text)
                right_quality = self._check_text_quality(right_text)
                self._last_quality_warnings = [
                    *self._last_quality_warnings,
                    "低可信场景已完成人工确认并继续比对",
                ]

            if left_quality.get("quality") == "bad" or right_quality.get("quality") == "bad":
                self._set_result_summary("文本层质量不足：已降级继续比对，结果仅供参考", warn=True)
                log_event(
                    "CMP_RESULT_UNRELIABLE",
                    "compare continues in degraded mode",
                    level="warning",
                    trace_id=self._trace_id,
                    left_quality=str(left_quality.get("quality")),
                    right_quality=str(right_quality.get("quality")),
                )
                self._last_quality_warnings = [
                    *self._last_quality_warnings,
                    "文本层质量过低且OCR未成功：已降级继续比对，建议复核结论。",
                ]

            # Get effective content/format mode.
            # Guard against any UI state drift: if format diffs are requested,
            # force non-pure-content mode for this comparison run.
            format_requested = self._btn_filter_format.isChecked()
            pure_content = self._btn_pure_content.isChecked()
            if format_requested and pure_content:
                self._btn_pure_content.setChecked(False)
                pure_content = False
                self._set_result_summary("已自动关闭“忽略空白”，以启用格式差异显示")
            self._show_format_diffs = bool(format_requested and not pure_content)
            if hasattr(self.left_view, "_image"):
                self.left_view._image.set_show_format_diffs(self._show_format_diffs)
            if hasattr(self.right_view, "_image"):
                self.right_view._image.set_show_format_diffs(self._show_format_diffs)
            self._last_pure_content_mode = pure_content
            print(f"[verbatim] Pure content mode: {pure_content}")

            # Get v1.0 normalization settings
            ignore_punct = self._btn_ignore_punctuation.isChecked()
            norm_numbers = self._btn_normalize_numbers.isChecked()
            merge_kv = self._btn_merge_keyvalue.isChecked()

            print(
                f"[verbatim] v1.0 normalization: ignore_punctuation={ignore_punct}, normalize_numbers={norm_numbers}, merge_key_value={merge_kv}"
            )

            ops, norm_log = diff_regions(
                left_region,
                right_region,
                pure_content_mode=pure_content,
                ignore_punctuation=ignore_punct,
                normalize_numbers=norm_numbers,
                merge_key_value_lines=merge_kv,
                suppress_trivial_diffs=not ocr_used,
                coalesce_nearby_text_ops=not ocr_used,
            )
            removed_noise = 0
            ops, removed_noise = self._filter_low_confidence_noise_ops(
                ops, left_text, right_text, left_quality, right_quality
            )
            if removed_noise > 0:
                print(f"[verbatim] Low-confidence noise filter removed {removed_noise} tiny content diffs.")
                note = f"低置信文本层已自动压制 {removed_noise} 条微小差异噪声。"
                self._last_quality_warnings = [*self._last_quality_warnings, note]
            ops, weak_removed = self._suppress_weak_confusable_ops(
                ops,
                left_text,
                right_text,
                ocr_used=ocr_used,
                left_quality=left_quality,
                right_quality=right_quality,
            )
            if weak_removed > 0:
                print(f"[verbatim] Weak-confusable filter removed {weak_removed} tiny replace diffs.")
                note = f"已压制 {weak_removed} 条低置信弱混淆差异（如OCR易混字符）。"
                self._last_quality_warnings = [*self._last_quality_warnings, note]

            blocked_low_reliability, blocked_reason = self._should_block_low_reliability_diffs(
                ops=ops,
                left_text=left_text,
                right_text=right_text,
                left_quality=left_quality,
                right_quality=right_quality,
                ocr_used=bool(ocr_used),
                left_ocr_applied=bool(left_ocr_applied),
                right_ocr_applied=bool(right_ocr_applied),
            )
            if blocked_low_reliability:
                print(f"[verbatim] Low-reliability guard: {blocked_reason}")
                self._last_quality_warnings = [*self._last_quality_warnings, blocked_reason]
                ops = []
            if compare_status == "REVIEW" and not manual_review_confirmed:
                print(f"[verbatim] Compare decision status: REVIEW ({compare_status_note})")
                ops = []
            visual_diff_used = False
            if not ops and compare_status != "PASS":
                visual_ops = self._build_visual_diff_ops()
                if visual_ops:
                    ops = visual_ops
                    visual_diff_used = True
                    self._last_decision_basis = "raster"
                    self._last_fallback_reason = "visual_diff_after_unreliable_text_or_ocr"
                    self._last_quality_warnings = [
                        *self._last_quality_warnings,
                        "文本/OCR链路低可信，已补充视觉差异检测结果。",
                    ]
                    log_event(
                        "CMP_DECISION_BASIS",
                        "compare decision basis switched to raster",
                        trace_id=self._trace_id,
                        basis="raster",
                        ops_count=len(ops),
                    )

            # Print normalization log (v1.0 搂6)
            print("\n" + "=" * 50)
            print(norm_log.to_string())
            print("=" * 50 + "\n")

            # ============================================================
            # v1.0 field-mapping integration (P0)
            # Run field-level comparison before character-level diff.
            # ============================================================
            def _guarded_field_mapping_enable(_left_text, _right_text, _left_kvs, _right_kvs):
                if compare_status != "PASS":
                    return False, "低可信场景关闭字段映射（人工复核）"
                if bool(ocr_used):
                    return False, "OCR场景默认关闭字段映射（降低误报）"
                if (
                    str(left_quality.get("quality", "good")) != "good"
                    or str(right_quality.get("quality", "good")) != "good"
                ):
                    return False, "文本质量非good，关闭字段映射"
                return should_enable_field_mapping(_left_text, _right_text, _left_kvs, _right_kvs)

            field_result = run_field_mapping(
                left_text=left_text,
                right_text=right_text,
                extract_key_values=extract_key_values,
                should_enable_field_mapping=_guarded_field_mapping_enable,
                compare_by_fields=compare_by_fields,
            )
            left_kvs = field_result.left_kvs
            right_kvs = field_result.right_kvs
            field_diffs = field_result.field_diffs

            if field_result.enabled:
                print("[verbatim] Field mapping results:")
                for diff in field_diffs:
                    print(f"  {format_field_diff_description(diff)}")
                self._last_field_mapping_note = ""
            else:
                print(f"[verbatim] Field mapping skipped: {field_result.disable_reason}")
                self._last_field_mapping_note = field_result.note

            self._last_field_diffs = field_diffs
            self._last_field_kvs = (left_kvs, right_kvs)

            if self._debug_log_text:
                print(json.dumps([op.to_dict() for op in ops], ensure_ascii=True, indent=2))
            else:
                print(f"[verbatim] Diff ops generated: {len(ops)}")

            self._last_diff_ops = list(ops)
            self._last_left_region = left_region
            self._last_right_region = right_region
            self._last_left_ocr_applied = bool(left_ocr_applied)
            self._last_right_ocr_applied = bool(right_ocr_applied)
            self._last_left_coords_reliable = bool(left_coords_reliable)
            self._last_right_coords_reliable = bool(right_coords_reliable)
            self._focused_diff_op = None
            self._rerender_diff_overlays(keep_focus=False)

            self._populate_diff_list(ops)
            rel = self._reliability_level(
                left_quality,
                right_quality,
                ocr_used=bool(ocr_used),
                ocr_errors=list(self._last_ocr_errors),
            )
            base_summary = build_compare_result_summary(
                left_ocr_applied=left_ocr_applied,
                right_ocr_applied=right_ocr_applied,
                dual_ocr_mode=bool(self._btn_dual_ocr.isChecked()),
                pure_content=pure_content,
                show_format_diffs=self._show_format_diffs,
            )
            state_label_map = {
                OcrRunState.SUCCESS: "成功",
                OcrRunState.PARTIAL: "部分成功",
                OcrRunState.FAILURE: "失败",
                OcrRunState.BLOCKED: "阻塞",
            }
            base_summary = f"{base_summary} | OCR状态: {state_label_map.get(self._last_ocr_state, '未知')}"
            if not use_ocr:
                base_summary = f"{base_summary} | OCR: 已禁用"
            elif not can_run_ocr:
                base_summary = f"{base_summary} | OCR: 条件不足"
            elif bool(use_ocr and can_run_ocr and not ocr_was_recommended):
                base_summary = f"{base_summary} | OCR: 已启用未触发"
            if bool(use_ocr and can_run_ocr and ocr_was_recommended and ocr_result.attempted_but_empty):
                base_summary = f"{base_summary} | OCR: 已尝试未成功"
            if not left_coords_reliable and not right_coords_reliable:
                base_summary = f"{base_summary} | 定位: 不可定位"
            elif not left_coords_reliable or not right_coords_reliable:
                base_summary = f"{base_summary} | 定位: 部分可定位"
            else:
                base_summary = f"{base_summary} | 定位: 可定位"
            if blocked_low_reliability or compare_status == "REVIEW":
                summary_text = f"{base_summary} | 状态:{compare_status} | 可信度:低 | 结论:需人工复核"
                summary_warn = True
            elif compare_status == "REFERENCE_ONLY":
                summary_text = f"{base_summary} | 状态:{compare_status} | 可信度:低 | 结论:仅供参考"
                summary_warn = True
            else:
                summary_text = f"{base_summary} | 状态:{compare_status} | 可信度:{rel}"
                summary_warn = rel != "高"
            self._last_decision_basis = self._decision_basis_for_compare(
                ocr_used=bool(ocr_used),
                visual_diff_used=bool(visual_diff_used),
            )
            self._last_compare_vm = CompareViewModel(
                summary=summary_text,
                warn=summary_warn,
                ocr_state=str(self._last_ocr_state),
                ocr_state_reason=str(self._last_ocr_state_reason),
                warnings=list(self._last_quality_warnings or []),
                decision_basis=str(self._last_decision_basis),
                gate_reason=str(self._last_gate_reason),
                fallback_reason=str(self._last_fallback_reason),
                quality_scores=getattr(self, "_last_quality_scores", None),
            )
            self._set_result_summary(summary_text, warn=summary_warn)
            try:
                COMPARE_HISTORY_MANAGER.add_record(
                    status="ok",
                    summary=self._last_compare_summary,
                    ops_count=len(ops),
                    field_diffs_count=len(field_diffs),
                    ocr_used=bool(ocr_used),
                    compare_status=str(compare_status),
                    reliability=str(rel),
                    ocr_state=str(self._last_ocr_state),
                    ocr_state_reason=str(self._last_ocr_state_reason),
                    decision_basis=str(self._last_decision_basis),
                    gate_reason=str(self._last_gate_reason),
                    fallback_reason=str(self._last_fallback_reason),
                    left_page=int(self._left_page_number),
                    right_page=int(self._right_page_number),
                    left_bbox=self._left_sel_bbox,
                    right_bbox=self._right_sel_bbox,
                    warnings_count=len(getattr(self, "_last_quality_warnings", [])),
                    diff_ops=[op.to_dict() for op in ops],
                    left_region_text=left_text,
                    right_region_text=right_text,
                    left_ocr_applied=bool(left_ocr_applied),
                    right_ocr_applied=bool(right_ocr_applied),
                )
                self._update_compare_history_list()
            except Exception as hist_err:
                print(f"[verbatim] Compare history save failed: {hist_err}")
            if self._prealign_active:
                step_note = f"预对齐人工修正步数: {self._prealign_manual_adjust_steps}"
                self._last_quality_warnings = [*self._last_quality_warnings, step_note]
                print(f"[verbatim] {step_note}")
            log_event(
                "CMP_RESULT_OK",
                "compare completed",
                ops_count=len(ops),
                field_diffs_count=len(field_diffs),
                ocr_used=bool(ocr_used),
                compare_status=str(compare_status),
                decision_basis=str(self._last_decision_basis),
                prealign_active=bool(self._prealign_active),
                prealign_manual_adjust_steps=int(self._prealign_manual_adjust_steps),
            )

            # Preserve the post-compare enabled state after the input lock is released.
            if isinstance(getattr(self, "_compare_input_state", None), dict):
                self._compare_input_state["_btn_save_selection"] = True
            self._btn_save_selection.setEnabled(True)
        finally:
            self._end_compare_feedback()

    def _populate_diff_list(self, ops) -> None:
        """Populate diff lists with categorized items."""
        first_focus = populate_diff_lists(
            self._content_diff_list,
            self._format_diff_list,
            self._diff_tab_widget,
            list(ops),
            compare_vm=self._last_compare_vm,
            fallback_quality_warnings=list(getattr(self, "_last_quality_warnings", [])),
            fallback_quality_scores=getattr(self, "_last_quality_scores", None),
            field_note=str(getattr(self, "_last_field_mapping_note", "")),
            field_diffs=getattr(self, "_last_field_diffs", None),
            pure_content_mode=bool(getattr(self, "_last_pure_content_mode", False)),
        )
        if not first_focus:
            return
        focus_type, focus_obj = first_focus
        if focus_type == "field_diff":
            self._focus_field_diff(focus_obj)
        elif focus_type == "char_diff" or focus_type == "format_diff":
            self._focus_op(focus_obj)

    def _focus_field_diff(self, fd: FieldDiff) -> None:
        """Highlight the field diff in viewers."""
        # Field-level diffs do not map reliably to a precise rectangle yet.
        # Clear current selection for now; precise highlighting can be added later.
        self.left_view.set_selected_overlays([])
        self.right_view.set_selected_overlays([])

        # Update diff details panel
        self._update_field_diff_details(fd)

    def _update_field_diff_details(self, fd: FieldDiff) -> None:
        """Update diff details panel for field-level diff."""
        self._diff_details.setHtml(build_field_diff_details_html(fd))

    def _on_diff_item_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if data is None:
            return

        def _is_stale_diff_op(op_obj) -> bool:
            # When page/document changes, old diff items should no longer drive focus.
            if not self._last_diff_ops:
                return True
            return op_obj not in self._last_diff_ops

        # Support the tuple payload format: (diff_type, diff_obj)
        if isinstance(data, tuple):
            diff_type, diff_obj = data
            if diff_type == "field_diff":
                self._focus_field_diff(diff_obj)
            elif diff_type == "field_match":
                self._focus_field_diff(diff_obj)  # field_match is also a FieldDiff
            elif diff_type == "char_diff" or diff_type == "format_diff":
                if _is_stale_diff_op(diff_obj):
                    self.statusBar().showMessage("差异已过期，请重新对比", 3000)
                    return
                self._focus_op(diff_obj)
                self._update_diff_details(diff_obj)
        else:
            # Backward compatibility: older items stored the DiffOp directly
            if _is_stale_diff_op(data):
                self.statusBar().showMessage("差异已过期，请重新对比", 3000)
                return
            self._focus_op(data)
            self._update_diff_details(data)

    def _focus_op(self, op) -> None:
        """Scroll viewers and highlight the selected op with bold border."""

        from core.models import DiffOpType

        self._focused_diff_op = op

        # Professional diff palette: restrained saturation, easy on the eyes.
        # Delete: dark red (#c0392b)
        # Add: deep green (#27ae60)
        # Replace: deep blue (#2980b9)
        # Format: dark gold (#b8860b)
        col_format = QColor(184, 134, 11, 240)  # dark gold - format diff
        col_add = QColor(39, 174, 96, 240)  # deep green - addition
        col_del = QColor(192, 57, 43, 240)  # dark red - deletion
        col_rep = QColor(41, 128, 185, 240)  # deep blue - replacement

        left_zoom = self._render_zoom_for("left")
        right_zoom = self._render_zoom_for("right")

        def left_bbox_to_qrectf(b: BBox) -> QRectF:
            x0, y0, x1, y1 = b
            return QRectF(
                float(x0) * left_zoom,
                float(y0) * left_zoom,
                float(x1 - x0) * left_zoom,
                float(y1 - y0) * left_zoom,
            )

        def right_bbox_to_qrectf(b: BBox) -> QRectF:
            x0, y0, x1, y1 = b
            return QRectF(
                float(x0) * right_zoom,
                float(y0) * right_zoom,
                float(x1 - x0) * right_zoom,
                float(y1 - y0) * right_zoom,
            )

        def _indices_to_bboxes(region: RegionData | None, indices: list[int]) -> list[BBox]:
            if region is None or not indices:
                return []
            idx_set = {int(i) for i in indices if int(i) >= 0}
            if not idx_set:
                return []
            bbs = [ch.bbox for ch in region.chars if int(ch.index) in idx_set]
            return bbs

        if op.type == DiffOpType.FORMAT_CHANGE:
            c = col_format
        elif op.type == DiffOpType.ADD:
            c = col_add
        elif op.type == DiffOpType.DEL:
            c = col_del
        elif op.type == DiffOpType.VISUAL_DIFF:
            c = QColor(142, 68, 173, 90)
        else:
            c = col_rep

        left_bboxes = list(
            op.left_bboxes or _indices_to_bboxes(self._last_left_region, getattr(op, "left_indices", []))
        )
        right_bboxes = list(
            op.right_bboxes or _indices_to_bboxes(self._last_right_region, getattr(op, "right_indices", []))
        )
        left_sel = [(left_bbox_to_qrectf(b), c) for b in left_bboxes]
        right_sel = [(right_bbox_to_qrectf(b), c) for b in right_bboxes]

        # OCR-applied side has no reliable source coordinates; avoid misleading focus overlays.
        left_coords_reliable = bool(self._last_left_coords_reliable) and not self._region_uses_synthetic_coords(
            self._last_left_region
        )
        right_coords_reliable = bool(self._last_right_coords_reliable) and not self._region_uses_synthetic_coords(
            self._last_right_region
        )

        if (self._last_left_ocr_applied and not self._last_left_ocr_has_coords) or not left_coords_reliable:
            left_sel = []
        if (self._last_right_ocr_applied and not self._last_right_ocr_has_coords) or not right_coords_reliable:
            right_sel = []

        self.left_view.set_selected_overlays(left_sel)
        self.right_view.set_selected_overlays(right_sel)

        if left_sel:
            self.left_view.scroll_to_rect(left_sel[0][0])
        if right_sel:
            self.right_view.scroll_to_rect(right_sel[0][0])

    def _rerender_diff_overlays(self, *, keep_focus: bool) -> bool:
        """Recompute diff overlays under current zoom from cached compare result."""
        if not self._last_diff_ops or self._last_left_region is None or self._last_right_region is None:
            if self._debug_spans and (self._last_left_spans or self._last_right_spans):
                self._render_spans_only()
                return True
            return False

        left_overlays, right_overlays, left_badges, right_badges = self._ops_to_overlays(
            self._last_diff_ops, self._last_left_region, self._last_right_region
        )
        debug_left_overlays: list[tuple[QRectF, QColor, str]] = []
        debug_right_overlays: list[tuple[QRectF, QColor, str]] = []
        if self._debug_spans:
            self._log_spans_render("left")
            self._log_spans_render("right")
            debug_color = QColor(142, 68, 173)
            debug_color.setAlpha(60)
            if self._last_left_spans and self._should_render_spans("left"):
                left_zoom = self._render_zoom_for("left")
                for sp in self._last_left_spans:
                    x0, y0, x1, y1 = sp.bbox
                    rect = QRectF(x0 * left_zoom, y0 * left_zoom, (x1 - x0) * left_zoom, (y1 - y0) * left_zoom)
                    debug_left_overlays.append((rect, debug_color, "debug"))
            if self._last_right_spans and self._should_render_spans("right"):
                right_zoom = self._render_zoom_for("right")
                for sp in self._last_right_spans:
                    x0, y0, x1, y1 = sp.bbox
                    rect = QRectF(x0 * right_zoom, y0 * right_zoom, (x1 - x0) * right_zoom, (y1 - y0) * right_zoom)
                    debug_right_overlays.append((rect, debug_color, "debug"))
        left_coords_reliable = bool(self._last_left_coords_reliable) and not self._region_uses_synthetic_coords(
            self._last_left_region
        )
        right_coords_reliable = bool(self._last_right_coords_reliable) and not self._region_uses_synthetic_coords(
            self._last_right_region
        )

        if (self._last_left_ocr_applied and not self._last_left_ocr_has_coords) or not left_coords_reliable:
            label = "OCR" if self._last_left_ocr_applied else "无"
            left_overlays, left_badges = [], self._unlocatable_badges("left", label=label)
        if (self._last_right_ocr_applied and not self._last_right_ocr_has_coords) or not right_coords_reliable:
            label = "OCR" if self._last_right_ocr_applied else "无"
            right_overlays, right_badges = [], self._unlocatable_badges("right", label=label)

        if debug_left_overlays:
            left_overlays = list(left_overlays) + debug_left_overlays
        if debug_right_overlays:
            right_overlays = list(right_overlays) + debug_right_overlays

        if hasattr(self.left_view._image, "set_overlays_with_types"):
            self.left_view._image.set_overlays_with_types(left_overlays)
            self.right_view._image.set_overlays_with_types(right_overlays)
        else:
            self.left_view.set_overlays([(r, c) for r, c, _ in left_overlays])
            self.right_view.set_overlays([(r, c) for r, c, _ in right_overlays])

        self.left_view.set_badges(left_badges)
        self.right_view.set_badges(right_badges)
        left_sel = []
        right_sel = []
        if self._left_sel_bbox:
            left_zoom = self._render_zoom_for("left")
            left_rect = QRectF(
                self._left_sel_bbox[0] * left_zoom,
                self._left_sel_bbox[1] * left_zoom,
                (self._left_sel_bbox[2] - self._left_sel_bbox[0]) * left_zoom,
                (self._left_sel_bbox[3] - self._left_sel_bbox[1]) * left_zoom,
            )
            left_sel = [(left_rect, QColor(243, 156, 18, 180))]
        if self._right_sel_bbox:
            right_zoom = self._render_zoom_for("right")
            right_rect = QRectF(
                self._right_sel_bbox[0] * right_zoom,
                self._right_sel_bbox[1] * right_zoom,
                (self._right_sel_bbox[2] - self._right_sel_bbox[0]) * right_zoom,
                (self._right_sel_bbox[3] - self._right_sel_bbox[1]) * right_zoom,
            )
            right_sel = [(right_rect, QColor(243, 156, 18, 180))]
        self.left_view.set_selected_overlays(left_sel)
        self.right_view.set_selected_overlays(right_sel)
        if left_sel:
            self.left_view.scroll_to_rect(left_sel[0][0])
        if right_sel:
            self.right_view.scroll_to_rect(right_sel[0][0])
        if keep_focus and self._focused_diff_op is not None:
            self._focus_op(self._focused_diff_op)
        return True

    def _bbox_close(self, a, b, eps: float = 1.0) -> bool:
        if not a or not b:
            return False
        try:
            return all(abs(float(a[i]) - float(b[i])) <= eps for i in range(4))
        except Exception:
            return False

    def _should_render_spans(self, side: str) -> bool:
        spans = self._last_left_spans if side == 'left' else self._last_right_spans
        if not spans:
            return False
        meta = self._last_left_spans_meta if side == 'left' else self._last_right_spans_meta
        if not meta:
            return True
        pdf = self._left_pdf if side == 'left' else self._right_pdf
        page = self._left_page_number if side == 'left' else self._right_page_number
        if not pdf:
            return False
        if str(pdf) != str(meta.get('pdf') or '') or int(page) != int(meta.get('page', -1)):
            return False
        sel = self._left_sel_bbox if side == 'left' else self._right_sel_bbox
        source_bbox = meta.get('source_bbox') or meta.get('clip_bbox')
        if sel is None:
            return True
        return self._bbox_close(sel, source_bbox)

    def _append_spans_unlocatable_warning(self, side: str, reason: str) -> None:
        label = "left" if side == "left" else "right"
        note = f"OCR spans generated but not locatable ({label}): {reason}"
        if note in (getattr(self, '_last_quality_warnings', []) or []):
            return
        self._last_quality_warnings = [*getattr(self, '_last_quality_warnings', []), note]

    def _render_spans_only(self) -> None:
        if not self._debug_spans:
            return
        self._log_spans_render("left")
        self._log_spans_render("right")
        debug_color = QColor(142, 68, 173)
        debug_color.setAlpha(60)
        left_overlays: list[tuple[QRectF, QColor, str]] = []
        right_overlays: list[tuple[QRectF, QColor, str]] = []
        if self._last_left_spans and self._should_render_spans("left"):
            left_zoom = self._render_zoom_for("left")
            for sp in self._last_left_spans:
                x0, y0, x1, y1 = sp.bbox
                rect = QRectF(x0 * left_zoom, y0 * left_zoom, (x1 - x0) * left_zoom, (y1 - y0) * left_zoom)
                left_overlays.append((rect, debug_color, "debug"))
        if self._last_right_spans and self._should_render_spans("right"):
            right_zoom = self._render_zoom_for("right")
            for sp in self._last_right_spans:
                x0, y0, x1, y1 = sp.bbox
                rect = QRectF(x0 * right_zoom, y0 * right_zoom, (x1 - x0) * right_zoom, (y1 - y0) * right_zoom)
                right_overlays.append((rect, debug_color, "debug"))

        if hasattr(self.left_view._image, "set_overlays_with_types"):
            self.left_view._image.set_overlays_with_types(left_overlays)
            self.right_view._image.set_overlays_with_types(right_overlays)
        else:
            self.left_view.set_overlays([(r, c) for r, c, _ in left_overlays])
            self.right_view.set_overlays([(r, c) for r, c, _ in right_overlays])

    def _log_spans_render(self, side: str) -> None:
        spans = self._last_left_spans if side == "left" else self._last_right_spans
        view = self.left_view if side == "left" else self.right_view
        pixmap = view._image.pixmap()
        if pixmap and not pixmap.isNull():
            canvas_w = int(pixmap.width())
            canvas_h = int(pixmap.height())
        else:
            canvas_w = 0
            canvas_h = 0
        scale = float(self._render_zoom_for(side))
        count = int(len(spans or []))
        key = (count, round(scale, 4), canvas_w, canvas_h)
        prev = self._last_spans_render_log.get(side)
        if prev == key:
            return
        self._last_spans_render_log[side] = key
        log_event(
            "SPANS_RENDER",
            f"[SPANS_RENDER] count={count} scale={scale:.2f} canvas=({canvas_w},{canvas_h})",
            trace_id=self._trace_id,
            side=side,
            count=count,
            scale=scale,
            canvas_w=canvas_w,
            canvas_h=canvas_h,
            coords_reliable=bool(
                self._last_left_coords_reliable if side == "left" else self._last_right_coords_reliable
            ),
            ocr_has_coords=bool(
                self._last_left_ocr_has_coords if side == "left" else self._last_right_ocr_has_coords
            ),
        )

    def _unlocatable_badges(self, side: str, *, label: str = "OCR") -> list[tuple[QRectF, str, QColor]]:
        if side == "left":
            bbox = self._left_sel_bbox
        else:
            bbox = self._right_sel_bbox
        if not bbox:
            return []
        zoom = self._render_zoom_for(side)
        rect = QRectF(
            bbox[0] * zoom,
            bbox[1] * zoom,
            (bbox[2] - bbox[0]) * zoom,
            (bbox[3] - bbox[1]) * zoom,
        )
        return [(rect, label, QColor(243, 156, 18))]

    def _ops_to_overlays(
        self, ops, left_region: RegionData, right_region: RegionData
    ) -> tuple[
        list[tuple[QRectF, QColor, str]],
        list[tuple[QRectF, QColor, str]],
        list[tuple[QRectF, str, QColor]],
        list[tuple[QRectF, str, QColor]],
    ]:
        """Convert diff ops to overlays and badges with block-level aggregation.

        Aggregates consecutive characters with same diff type into single blocks
        to reduce visual noise ("badge explosion").

        Returns:
            (left_overlays, right_overlays, left_badges, right_badges)
        """
        from core.models import DiffOpType

        left_zoom = self._render_zoom_for("left")
        right_zoom = self._render_zoom_for("right")

        def bbox_to_qrectf(b: BBox, *, side: str) -> QRectF:
            z = left_zoom if side == "left" else right_zoom
            x0, y0, x1, y1 = b
            return QRectF(
                float(x0) * z,
                float(y0) * z,
                float(x1 - x0) * z,
                float(y1 - y0) * z,
            )

        def union_bbox(bbs: list[BBox]) -> BBox:
            if not bbs:
                return (0.0, 0.0, 0.0, 0.0)
            x0 = min(b[0] for b in bbs)
            y0 = min(b[1] for b in bbs)
            x1 = max(b[2] for b in bbs)
            y1 = max(b[3] for b in bbs)
            return (float(x0), float(y0), float(x1), float(y1))

        def aggregate_bboxes_by_line(
            bboxes: list[BBox],
            *,
            line_threshold: float = 5.0,
            gap_multiplier: float = 3.0,
        ) -> list[BBox]:
            """Aggregate bboxes that are on the same line and close together.

            This creates block-level highlights instead of per-character highlights.
            """
            if not bboxes:
                return []

            # Sort by y position (top), then x position (left)
            sorted_bboxes = sorted(bboxes, key=lambda b: (b[1], b[0]))

            aggregated = []
            current_line: list[BBox] = [sorted_bboxes[0]]
            current_y = sorted_bboxes[0][1]

            for bbox in sorted_bboxes[1:]:
                # Check if this bbox is on the same line (similar y position)
                y_diff = abs(bbox[1] - current_y)

                # Also check horizontal proximity
                if current_line:
                    last_bbox = current_line[-1]
                    x_gap = bbox[0] - last_bbox[2]  # Distance from last char's right edge
                else:
                    x_gap = 0

                # Same line and reasonably close horizontally (within N char widths)
                avg_char_width = (bbox[2] - bbox[0]) if (bbox[2] - bbox[0]) > 0 else 10
                is_same_line = y_diff < line_threshold
                is_close_enough = x_gap < avg_char_width * gap_multiplier

                if is_same_line and is_close_enough:
                    current_line.append(bbox)
                else:
                    # Finalize current line and start new one
                    if current_line:
                        aggregated.append(union_bbox(current_line))
                    current_line = [bbox]
                    current_y = bbox[1]

            # Don't forget the last line
            if current_line:
                aggregated.append(union_bbox(current_line))

            return aggregated

        def indices_to_aggregated_bboxes(region: RegionData, indices: list[int]) -> list[BBox]:
            """Convert CharData.index list -> aggregated bboxes (block-level)."""
            if not indices:
                return []
            idx_set = {int(i) for i in indices if int(i) >= 0}
            if not idx_set:
                return []
            selected = [ch for ch in region.chars if int(ch.index) in idx_set]
            if not selected:
                return []

            # Get all bboxes
            all_bboxes = [ch.bbox for ch in selected]

            # OCR spans are approximate; use tighter aggregation to improve precision.
            if self._region_uses_ocr_boxes(region):
                return aggregate_bboxes_by_line(
                    all_bboxes,
                    line_threshold=2.5,
                    gap_multiplier=1.5,
                )
            return aggregate_bboxes_by_line(all_bboxes)

        # Professional diff palette with translucent overlays.
        # Delete: dark red (#c0392b, alpha 80)
        # Add: deep green (#27ae60, alpha 80)
        # Replace: deep blue (#2980b9, alpha 80)
        # Format: dark gold (#b8860b, alpha 60)
        col_del = QColor(192, 57, 43, 80)  # dark red - deletion
        col_add = QColor(39, 174, 96, 80)  # deep green - addition
        col_rep = QColor(41, 128, 185, 80)  # deep blue - replacement
        col_format = QColor(184, 134, 11, 60)  # dark gold - format diff
        col_visual = QColor(142, 68, 173, 90)  # visual/raster diff
        col_unloc = QColor(243, 156, 18, 70)  # 橙色- 不可定位提示
        badge_unloc = QColor(243, 156, 18)

        left_overlays: list[tuple[QRectF, QColor, str]] = []
        right_overlays: list[tuple[QRectF, QColor, str]] = []
        left_badges: list[tuple[QRectF, str, QColor]] = []
        right_badges: list[tuple[QRectF, str, QColor]] = []
        unloc_left = False
        unloc_right = False
        format_visible = bool(self._show_format_diffs)

        def selection_anchor_bbox(side: str) -> BBox | None:
            if side == "left":
                if self._left_sel_bbox:
                    return self._left_sel_bbox
                if left_region.bboxes:
                    return union_bbox(left_region.bboxes)
            if side == "right":
                if self._right_sel_bbox:
                    return self._right_sel_bbox
                if right_region.bboxes:
                    return union_bbox(right_region.bboxes)
            return None

        for op in ops:
            if op.type == DiffOpType.FORMAT_CHANGE:
                # Format diffs use dark-gold highlighting.
                color = col_format
                badge_text = "格"
                badge_color = QColor(184, 134, 11)  # dark gold
                diff_type = "format"

                # Aggregate format bboxes by line
                left_bboxes = aggregate_bboxes_by_line(op.left_bboxes) if op.left_bboxes else []
                right_bboxes = aggregate_bboxes_by_line(op.right_bboxes) if op.right_bboxes else []

                for b in left_bboxes:
                    rect = bbox_to_qrectf(b, side="left")
                    left_overlays.append((rect, color, diff_type))

                for b in right_bboxes:
                    rect = bbox_to_qrectf(b, side="right")
                    right_overlays.append((rect, color, diff_type))

                # Add single badge per aggregated block (not per character)
                if left_bboxes:
                    left_badges.append((bbox_to_qrectf(left_bboxes[0], side="left"), badge_text, badge_color))
                elif format_visible:
                    unloc_left = True
                if right_bboxes:
                    right_badges.append((bbox_to_qrectf(right_bboxes[0], side="right"), badge_text, badge_color))
                elif format_visible:
                    unloc_right = True

            elif op.type == DiffOpType.DEL:
                # Deletions use dark-red highlighting.
                color = col_del
                badge_text = "删"
                badge_color = QColor(192, 57, 43)  # dark red
                diff_type = "content"

                bboxes = (
                    op.left_bboxes if op.left_bboxes else indices_to_aggregated_bboxes(left_region, op.left_indices)
                )
                for b in bboxes:
                    rect = bbox_to_qrectf(b, side="left")
                    left_overlays.append((rect, color, diff_type))

                if bboxes:
                    left_badges.append((bbox_to_qrectf(bboxes[0], side="left"), badge_text, badge_color))
                else:
                    unloc_left = True

            elif op.type == DiffOpType.ADD:
                # Additions use deep-green highlighting.
                color = col_add
                badge_text = "增"
                badge_color = QColor(39, 174, 96)  # deep green
                diff_type = "content"

                bboxes = (
                    op.right_bboxes if op.right_bboxes else indices_to_aggregated_bboxes(right_region, op.right_indices)
                )
                for b in bboxes:
                    rect = bbox_to_qrectf(b, side="right")
                    right_overlays.append((rect, color, diff_type))

                if bboxes:
                    right_badges.append((bbox_to_qrectf(bboxes[0], side="right"), badge_text, badge_color))
                else:
                    unloc_right = True
                if not bboxes and self._last_right_ocr_applied and not self._last_right_ocr_has_coords:
                    anchor = selection_anchor_bbox("left")
                    if anchor:
                        left_overlays.append((bbox_to_qrectf(anchor, side="left"), col_unloc, "content"))
                        left_badges.append((bbox_to_qrectf(anchor, side="left"), badge_text, badge_color))

            elif op.type == DiffOpType.REPLACE:
                # Replacements use deep-blue highlighting.
                color = col_rep
                badge_text = "改"
                badge_color = QColor(41, 128, 185)  # deep blue
                diff_type = "content"

                left_bboxes = (
                    op.left_bboxes if op.left_bboxes else indices_to_aggregated_bboxes(left_region, op.left_indices)
                )
                for b in left_bboxes:
                    rect = bbox_to_qrectf(b, side="left")
                    left_overlays.append((rect, color, diff_type))

                right_bboxes = (
                    op.right_bboxes if op.right_bboxes else indices_to_aggregated_bboxes(right_region, op.right_indices)
                )
                for b in right_bboxes:
                    rect = bbox_to_qrectf(b, side="right")
                    right_overlays.append((rect, color, diff_type))

                if left_bboxes:
                    left_badges.append((bbox_to_qrectf(left_bboxes[0], side="left"), badge_text, badge_color))
                else:
                    unloc_left = True
                if right_bboxes:
                    right_badges.append((bbox_to_qrectf(right_bboxes[0], side="right"), badge_text, badge_color))
                else:
                    unloc_right = True

            elif op.type == DiffOpType.VISUAL_DIFF:
                color = col_visual
                badge_text = "V"
                badge_color = QColor(142, 68, 173)
                diff_type = "content"

                left_bboxes = list(op.left_bboxes or [])
                for b in left_bboxes:
                    rect = bbox_to_qrectf(b, side="left")
                    left_overlays.append((rect, color, diff_type))

                right_bboxes = list(op.right_bboxes or [])
                for b in right_bboxes:
                    rect = bbox_to_qrectf(b, side="right")
                    right_overlays.append((rect, color, diff_type))

                if left_bboxes:
                    left_badges.append((bbox_to_qrectf(left_bboxes[0], side="left"), badge_text, badge_color))
                else:
                    unloc_left = True
                if right_bboxes:
                    right_badges.append((bbox_to_qrectf(right_bboxes[0], side="right"), badge_text, badge_color))
                else:
                    unloc_right = True

        if unloc_left:
            anchor = selection_anchor_bbox("left")
            if anchor:
                left_overlays.append((bbox_to_qrectf(anchor, side="left"), col_unloc, "content"))
                left_badges.append((bbox_to_qrectf(anchor, side="left"), "无", badge_unloc))
        if unloc_right:
            anchor = selection_anchor_bbox("right")
            if anchor:
                right_overlays.append((bbox_to_qrectf(anchor, side="right"), col_unloc, "content"))
                right_badges.append((bbox_to_qrectf(anchor, side="right"), "无", badge_unloc))

        return left_overlays, right_overlays, left_badges, right_badges

    def _update_compare_history_list(self) -> None:
        if not hasattr(self, "_compare_history_list"):
            return
        self._compare_history_list.clear()
        for rec in COMPARE_HISTORY_MANAGER.list_records()[:100]:
            ts = rec.timestamp.strftime("%H:%M:%S")
            item_text = (
                f"{ts} | {rec.compare_status} | {rec.decision_basis} | OCR:{rec.ocr_state} | "
                f"差异{rec.ops_count}条 | L{rec.left_page + 1}->R{rec.right_page + 1}"
            )
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, rec.to_dict())
            if rec.compare_status != "PASS":
                item.setForeground(QColor("#8a5a00"))
            self._compare_history_list.addItem(item)

    @staticmethod
    def _diff_op_from_dict(data: dict) -> DiffOp:
        return DiffOp(
            type=DiffOpType(str(data.get("type", "replace"))),
            left_indices=list(data.get("left_indices", [])),
            right_indices=list(data.get("right_indices", [])),
            left_bboxes=[tuple(b) for b in data.get("left_bboxes", [])],  # type: ignore[arg-type]
            right_bboxes=[tuple(b) for b in data.get("right_bboxes", [])],  # type: ignore[arg-type]
            meta=dict(data.get("meta", {})),
        )

    def _on_compare_history_item_clicked(self, item: QListWidgetItem) -> None:
        payload = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(payload, dict):
            return
        self._replay_compare_history_payload(payload)

    def _replay_compare_history_payload(self, payload: dict) -> None:
        if self._left_pdf is None or self._right_pdf is None:
            self._set_result_summary("请先加载左右文档，再回放比对历史", warn=True)
            return

        left_page = int(payload.get("left_page", self._left_page_number))
        right_page = int(payload.get("right_page", self._right_page_number))
        self._left_page_number = self._clamp_page_index("left", left_page)
        self._right_page_number = self._clamp_page_index("right", right_page)
        self._left_committed_page_number = self._left_page_number
        self._right_committed_page_number = self._right_page_number
        self._sync_page_controls("left")
        self._sync_page_controls("right")

        self._load_page_data()
        self._load_into_view(self.left_view, self._left_pdf, page_number=self._left_page_number, side="left")
        self._load_into_view(self.right_view, self._right_pdf, page_number=self._right_page_number, side="right")

        left_bbox_raw = payload.get("left_bbox")
        right_bbox_raw = payload.get("right_bbox")
        self._left_sel_bbox = tuple(left_bbox_raw) if left_bbox_raw else None  # type: ignore[assignment]
        self._right_sel_bbox = tuple(right_bbox_raw) if right_bbox_raw else None  # type: ignore[assignment]
        self._apply_selected_bboxes()
        self._update_compare_enabled()

        op_dicts = payload.get("diff_ops") or []
        ops = []
        for od in op_dicts:
            if isinstance(od, dict):
                try:
                    ops.append(self._diff_op_from_dict(od))
                except Exception:
                    continue

        left_region_text = str(payload.get("left_region_text", "") or "")
        right_region_text = str(payload.get("right_region_text", "") or "")
        if left_region_text:
            self._last_left_region = self._build_region_from_text(left_region_text, self._left_page_number)
        else:
            self._last_left_region = None
        if right_region_text:
            self._last_right_region = self._build_region_from_text(right_region_text, self._right_page_number)
        else:
            self._last_right_region = None

        self._last_diff_ops = list(ops)
        self._last_left_ocr_applied = bool(payload.get("left_ocr_applied", False))
        self._last_right_ocr_applied = bool(payload.get("right_ocr_applied", False))
        self._last_decision_basis = str(payload.get("decision_basis", "text"))
        self._last_gate_reason = str(payload.get("gate_reason", ""))
        self._last_fallback_reason = str(payload.get("fallback_reason", ""))
        self._last_left_ocr_has_coords = False
        self._last_right_ocr_has_coords = False
        self._last_left_coords_reliable = not self._last_left_ocr_applied
        self._last_right_coords_reliable = not self._last_right_ocr_applied
        self._focused_diff_op = None
        if self._last_left_region is not None and self._last_right_region is not None:
            self._rerender_diff_overlays(keep_focus=False)
        else:
            self.left_view.set_overlays([])
            self.right_view.set_overlays([])
            self.left_view.set_badges([])
            self.right_view.set_badges([])
            self.left_view.set_hover_overlay(None)
            self.right_view.set_hover_overlay(None)
        self._populate_diff_list(ops)
        summary_text = str(payload.get("summary", "已回放历史比对结果"))
        summary_warn = str(payload.get("compare_status", "PASS")) != "PASS"
        self._last_compare_vm = CompareViewModel(
            summary=summary_text,
            warn=summary_warn,
            ocr_state=str(payload.get("ocr_state", "unknown")),
            ocr_state_reason=str(payload.get("ocr_state_reason", "")),
            warnings=[],
            decision_basis=str(payload.get("decision_basis", "text")),
            gate_reason=str(payload.get("gate_reason", "")),
            fallback_reason=str(payload.get("fallback_reason", "")),
            quality_scores=None,
        )
        self._set_result_summary(summary_text, warn=summary_warn)

    # Region management methods
    def _update_region_history(self) -> None:
        """Update the region history list."""
        self._region_list.clear()

        selections = REGION_MANAGER.list_selections()
        for sel in selections:
            item_text = f"L{sel.left_page + 1} -> R{sel.right_page + 1} | {sel.name}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, sel.name)
            self._region_list.addItem(item)

    def _on_region_item_clicked(self, item: QListWidgetItem) -> None:
        """Handle region history item click."""
        region_name = item.data(Qt.ItemDataRole.UserRole)
        selection = REGION_MANAGER.get_selection(region_name)

        if selection:
            # Load and display the saved selection
            self._load_selection(selection)
            self._btn_delete_region.setEnabled(True)

    def _load_selection(self, selection) -> None:
        """Load and display a saved selection."""
        # Set page numbers
        self._left_page_number = self._clamp_page_index("left", selection.left_page)
        self._right_page_number = self._clamp_page_index("right", selection.right_page)
        self._left_committed_page_number = self._left_page_number
        self._right_committed_page_number = self._right_page_number
        self._sync_page_controls("left")
        self._sync_page_controls("right")

        # Load page data if needed
        self._load_page_data()

        # Set selection bboxes
        self._left_sel_bbox = selection.left_bbox
        self._right_sel_bbox = selection.right_bbox

        # Update UI
        if self._left_pdf:
            self.left_view.set_title(f"左侧: {self._left_pdf.name} (第{self._left_page_number + 1}页)")
        if self._right_pdf:
            self.right_view.set_title(f"右侧: {self._right_pdf.name} (第{self._right_page_number + 1}页)")

        # Update UI
        self._sync_page_controls("left")
        self._sync_page_controls("right")

        # Render the pages
        if self._left_pdf:
            self._load_into_view(self.left_view, self._left_pdf, page_number=self._left_page_number, side="left")
        if self._right_pdf:
            self._load_into_view(self.right_view, self._right_pdf, page_number=self._right_page_number, side="right")

        # Enable compare button
        self._update_compare_enabled()

        # Show selection overlays
        if self._left_sel_bbox:
            left_zoom = self._render_zoom_for("left")
            left_rect = QRectF(
                self._left_sel_bbox[0] * left_zoom,
                self._left_sel_bbox[1] * left_zoom,
                (self._left_sel_bbox[2] - self._left_sel_bbox[0]) * left_zoom,
                (self._left_sel_bbox[3] - self._left_sel_bbox[1]) * left_zoom,
            )
            self.left_view.set_selected_overlays([(left_rect, QColor(52, 152, 219, 180))])

        if self._right_sel_bbox:
            right_zoom = self._render_zoom_for("right")
            right_rect = QRectF(
                self._right_sel_bbox[0] * right_zoom,
                self._right_sel_bbox[1] * right_zoom,
                (self._right_sel_bbox[2] - self._right_sel_bbox[0]) * right_zoom,
                (self._right_sel_bbox[3] - self._right_sel_bbox[1]) * right_zoom,
            )
            self.right_view.set_selected_overlays([(right_rect, QColor(46, 204, 113, 180))])

    def _save_current_selection(self) -> None:
        """Save current selection to region history."""
        if not (self._left_sel_bbox and self._right_sel_bbox):
            return

        # Ask user for name
        name, ok = QInputDialog.getText(
            self,
            "保存选区",
            "请输入当前选区名称：",
            text=f"选区_L{self._left_page_number + 1}_R{self._right_page_number + 1}",
        )

        if ok and name:
            # Save the selection
            REGION_MANAGER.add_selection(
                name=name,
                left_page=self._left_page_number,
                left_bbox=self._left_sel_bbox,
                right_page=self._right_page_number,
                right_bbox=self._right_sel_bbox,
            )

            # Update UI
            self._update_region_history()
            self._btn_delete_region.setEnabled(True)

    def _delete_selected_region(self) -> None:
        """Delete selected region from history."""
        current_item = self._region_list.currentItem()
        if current_item:
            region_name = current_item.data(Qt.ItemDataRole.UserRole)
            REGION_MANAGER.delete_selection(region_name)
            self._update_region_history()
            self._btn_delete_region.setEnabled(False)

    def _update_diff_details(self, op) -> None:
        """Update the diff details panel with selected operation details."""
        self._diff_details.setHtml(build_diff_details_html(op))

    def _calculate_connection_lines(
        self, left_overlays: list[tuple[QRectF, QColor]], right_overlays: list[tuple[QRectF, QColor]]
    ) -> list[tuple[QPointF, QPointF, QColor]]:
        """Calculate connection lines between corresponding overlays."""
        connection_lines = []

        # Simple strategy: connect center points of similar colored overlays
        left_rects = [rect for rect, color in left_overlays]

        # Group by color
        color_groups: dict[tuple[int, int, int], dict[str, list[QRectF]]] = {}
        for rect, color in left_overlays + right_overlays:
            color_key = (color.red(), color.green(), color.blue())
            if color_key not in color_groups:
                color_groups[color_key] = {"left": [], "right": []}
            if rect in left_rects:
                color_groups[color_key]["left"].append(rect)
            else:
                color_groups[color_key]["right"].append(rect)

        # Draw lines for each color group
        for color_key, rects in color_groups.items():
            left_group = rects["left"]
            right_group = rects["right"]

            if left_group and right_group:
                # Simple 1:1 mapping
                for i, left_rect in enumerate(left_group):
                    if i < len(right_group):
                        right_rect = right_group[i]

                        # Calculate center points
                        left_center = QPointF(left_rect.center().x(), left_rect.center().y())
                        right_center = QPointF(right_rect.center().x(), right_rect.center().y())

                        color = QColor(color_key[0], color_key[1], color_key[2], 150)
                        connection_lines.append((left_center, right_center, color))

        return connection_lines


def main() -> int:
    # Best-effort UTF-8 console setup on Windows to avoid mojibake in logs.
    if os.name == "nt":
        try:
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    app = QApplication(sys.argv)

    # Phase 14 Step 1: start empty; user selects PDFs via UI buttons.
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
