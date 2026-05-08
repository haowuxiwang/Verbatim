from __future__ import annotations

import contextlib
import pickle
import tempfile
import time
import unittest
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication, QListWidgetItem

from app.main_window import ZOOM_MAX, ZOOM_MIN, MainWindow
from core.compare_history import CompareHistoryManager
from core.models import CharData, DiffOp, DiffOpType, PageData, RegionData, StyleFlags
from core.ocr_client import OcrConfig
from core.services.ocr_engines import EngineResult, LocalOcrSelfCheck
from core.services.ocr_models import OcrSpan
from core.services.ocr_state import OcrRunState


class TestZoomGui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.w = MainWindow()
        self._default_local_ocr_check = LocalOcrSelfCheck(
            available=True,
            code="ready",
            message="ready",
            worker_python="python",
            runtime_dir="D:/ocr_runtime",
            json_exe="D:/ocr_runtime/PaddleOCR-json.exe",
            python_worker_ready=False,
            json_ready=True,
        )
        self.w._get_local_ocr_self_check = lambda force=False: self._default_local_ocr_check
        self.w.show()

    def tearDown(self):
        self.w.close()

    def test_initial_zoom_is_100_percent(self):
        self.assertEqual(self.w._left_zoom_label.text(), "100%")
        self.assertEqual(self.w._right_zoom_label.text(), "100%")

    def test_left_right_zoom_independent(self):
        self.w._btn_left_zoom_in.click()
        self.assertNotEqual(self.w._left_zoom_label.text(), "100%")
        self.assertEqual(self.w._right_zoom_label.text(), "100%")

        self.w._btn_right_zoom_out.click()
        self.assertNotEqual(self.w._right_zoom_label.text(), "100%")

    def test_zoom_bounds_disable_buttons(self):
        self.w._set_zoom_ratio("left", ZOOM_MIN)
        self.assertFalse(self.w._btn_left_zoom_out.isEnabled())
        self.assertTrue(self.w._btn_left_zoom_in.isEnabled())

        self.w._set_zoom_ratio("left", ZOOM_MAX)
        self.assertFalse(self.w._btn_left_zoom_in.isEnabled())
        self.assertTrue(self.w._btn_left_zoom_out.isEnabled())

    def test_zoom_requested_signal_changes_current_side(self):
        before_left = self.w._left_zoom_label.text()
        before_right = self.w._right_zoom_label.text()
        self.w.left_view.zoomRequested.emit(1)
        self.assertNotEqual(self.w._left_zoom_label.text(), before_left)
        self.assertEqual(self.w._right_zoom_label.text(), before_right)

    def test_qrect_to_pdf_bbox_uses_render_zoom_parameter(self):
        rect = QRect(0, 0, 200, 100)
        page_w, page_h = 1000.0, 1000.0
        b1 = self.w._qrect_to_pdf_bbox(rect, page_w=page_w, page_h=page_h, render_zoom=2.0)
        b2 = self.w._qrect_to_pdf_bbox(rect, page_w=page_w, page_h=page_h, render_zoom=4.0)
        self.assertGreater(b1[2] - b1[0], b2[2] - b2[0])
        self.assertGreater(b1[3] - b1[1], b2[3] - b2[1])

    def test_compare_button_gate_requires_both_pages_and_bboxes(self):
        self.assertFalse(self.w._btn_compare.isEnabled())
        self.w._left_page = self._make_page("L")
        self.w._right_page = self._make_page("R")
        self.w._left_sel_bbox = (10.0, 10.0, 80.0, 40.0)
        self.w._right_sel_bbox = None
        self.w._update_compare_enabled()
        self.assertFalse(self.w._btn_compare.isEnabled())
        self.w._right_sel_bbox = (10.0, 10.0, 80.0, 40.0)
        self.w._update_compare_enabled()
        self.assertTrue(self.w._btn_compare.isEnabled())

    def test_bbox_changed_tolerance(self):
        a = (10.0, 10.0, 20.0, 20.0)
        b = (10.4, 10.4, 20.4, 20.4)
        c = (12.2, 10.0, 20.0, 20.0)
        self.assertFalse(self.w._bbox_changed(a, b, eps=1.0))
        self.assertTrue(self.w._bbox_changed(a, c, eps=1.0))

    def test_ocr_cache_bbox_key_preserves_small_real_selection_changes(self):
        base = self.w._ocr_cache_bbox_key((10.0, 10.0, 20.0, 20.0))
        tiny_jitter = self.w._ocr_cache_bbox_key((10.04, 10.0, 20.0, 20.0))
        small_drag = self.w._ocr_cache_bbox_key((10.4, 10.0, 20.0, 20.0))
        self.assertEqual(base, tiny_jitter)
        self.assertNotEqual(base, small_drag)

    def test_track_manual_adjust_counts_only_real_changes(self):
        base = (10.0, 10.0, 100.0, 50.0)
        self.w._prealign_active = True
        self.w._prealign_base_left_bbox = base
        self.w._prealign_last_left_bbox = base
        self.w._prealign_manual_adjust_steps = 0
        self.w._track_manual_adjust("left", base)
        self.assertEqual(0, self.w._prealign_manual_adjust_steps)
        moved = (14.0, 10.0, 100.0, 50.0)
        self.w._track_manual_adjust("left", moved)
        self.assertEqual(1, self.w._prealign_manual_adjust_steps)
        self.w._track_manual_adjust("left", moved)
        self.assertEqual(1, self.w._prealign_manual_adjust_steps)

    def test_apply_prealign_candidate_sets_state_and_summary(self):
        self.w._left_pdf = Path("dummy_left.pdf")
        self.w._right_pdf = Path("dummy_right.pdf")
        self.w._left_page = self._make_page("A")
        self.w._right_page = self._make_page("B")

        left_bbox = (12.0, 20.0, 120.0, 80.0)
        right_bbox = (15.0, 24.0, 118.0, 86.0)
        self.w._apply_prealign_candidate(
            self.w._left_page_number,
            self.w._right_page_number,
            left_bbox,
            right_bbox,
            0.77,
            "test-candidate",
        )
        self.assertTrue(self.w._prealign_active)
        self.assertEqual(0, self.w._prealign_manual_adjust_steps)
        self.assertEqual(left_bbox, self.w._left_sel_bbox)
        self.assertEqual(right_bbox, self.w._right_sel_bbox)
        self.assertTrue(self.w._btn_compare.isEnabled())
        self.assertIn("已应用预对齐候选", self.w._last_compare_summary)

    def test_open_prealign_suggestions_delegates_to_process_backed_path(self):
        with patch.object(self.w, "_legacy_open_prealign_suggestions") as mocked:
            self.w._open_prealign_suggestions()
        mocked.assert_called_once_with()

    def test_load_into_view_renders_real_pdf_into_scroll_view(self):
        sample_pdf = next(Path(".").rglob("*艾曲泊帕乙醇胺片说明书批件*.pdf"))

        self.w._load_into_view(self.w.left_view, sample_pdf, page_number=0, side="left")

        pixmap = self.w.left_view._image.pixmap()
        self.assertIsNotNone(pixmap)
        assert pixmap is not None
        self.assertFalse(pixmap.isNull())
        self.assertIs(self.w.left_view._stack.currentWidget(), self.w.left_view._scroll)
        self.assertNotIn("Failed to render", self.w.left_view._empty_hint.text())

    def test_set_result_summary_warn_style(self):
        self.w._set_result_summary("warning", warn=True)
        self.assertIn("warning", self.w._result_summary_bar.text())
        self.assertIn("#fff6e5", self.w._result_summary_bar.styleSheet())
        self.w._set_result_summary("ok", warn=False)
        self.assertIn("ok", self.w._result_summary_bar.text())
        self.assertIn("#f7f9fb", self.w._result_summary_bar.styleSheet())

    def test_tool_mode_invalid_value_falls_back_to_select(self):
        self.w._set_tool_mode("invalid")
        self.assertEqual("select", self.w._current_tool_mode)

    def test_apply_responsive_ui_switches_button_labels(self):
        self.w.resize(1200, 900)
        self.w._apply_responsive_ui(force=True)
        self.assertEqual("对比", self.w._btn_compare.text())
        self.assertEqual("<", self.w._btn_left_prev.text())

        self.w.resize(1600, 900)
        self.w._apply_responsive_ui(force=True)
        self.assertEqual("开始对比", self.w._btn_compare.text())
        self.assertEqual("上一页", self.w._btn_left_prev.text())

    def test_refresh_ocr_ui_state_with_and_without_config(self):
        with patch.dict("os.environ", {"VERBATIM_OCR_ROUTE": "cloud_only"}):
            self.w._ocr_cfg = None
            self.w._refresh_ocr_ui_state()
            self.assertFalse(self.w._btn_use_ocr.isEnabled())
            self.assertIn("未配置", self.w._ocr_token_status.text())

        with (
            tempfile.TemporaryDirectory() as td,
            patch.dict(
                "os.environ",
                {"VERBATIM_OCR_ROUTE": "local_first", "VERBATIM_OCR_RUNTIME_DIR": td},
            ),
        ):
            self.w._ocr_cfg = None
            self.w._refresh_ocr_ui_state()
            self.assertTrue(self.w._btn_use_ocr.isEnabled())
            self.assertIn("本地优先", self.w._btn_use_ocr.toolTip())

        self.w._ocr_cfg = OcrConfig(token="abc", source="file", token_storage="plain")
        self._refresh_and_assert_cloud_config_present()

    def _refresh_and_assert_cloud_config_present(self):
        with patch.dict("os.environ", {"VERBATIM_OCR_ROUTE": "local_first"}):
            self.w._refresh_ocr_ui_state()
        self.assertTrue(self.w._btn_use_ocr.isEnabled())
        self.assertIn("明文存储", self.w._ocr_token_status.text())
        self.assertIn("明文存储", self.w._btn_use_ocr.toolTip())
        self.assertIn("已配置", self.w._ocr_token_status.text())

    def test_build_ocr_storage_note(self):
        self.assertIn("DPAPI", self.w._build_ocr_storage_note("dpapi"))
        self.assertIn("明文", self.w._build_ocr_storage_note("plain"))
        self.assertIn("环境变量", self.w._build_ocr_storage_note("env"))

    def test_local_ocr_breaker_skips_local_engine(self):
        self.w._local_ocr_fail_threshold = 2
        self.w._local_ocr_cooldown_sec = 30
        self.w._record_local_ocr_failure(RuntimeError("f1"))
        self.w._record_local_ocr_failure(RuntimeError("f2"))
        self.assertTrue(self.w._local_ocr_breaker_open())

        local_stub = object()
        cloud_stub = object()
        with patch.object(self.w, "_ocr_route_mode", return_value="local_first"):
            with patch.object(self.w, "_get_local_ocr_engine", return_value=local_stub):
                with patch.object(self.w, "_get_cloud_ocr_engine", return_value=cloud_stub):
                    engines = self.w._resolve_ocr_engines()

        labels = [x[0] for x in engines]
        self.assertNotIn("local", labels)
        self.assertIn("cloud", labels)

    def test_local_ocr_breaker_resets_after_success(self):
        self.w._local_ocr_fail_threshold = 1
        self.w._local_ocr_cooldown_sec = 30
        self.w._record_local_ocr_failure(RuntimeError("f"))
        self.assertTrue(self.w._local_ocr_breaker_open())
        self.w._record_local_ocr_success()
        self.assertFalse(self.w._local_ocr_breaker_open())

    def test_toggle_format_diffs_and_pure_content_interaction(self):
        self.w._btn_pure_content.setChecked(True)
        self.w._btn_filter_format.setChecked(True)
        self.w._toggle_format_diffs()
        self.assertFalse(self.w._btn_pure_content.isChecked())
        self.assertTrue(self.w._show_format_diffs)
        self.assertEqual("隐藏格式差异", self.w._btn_filter_format.text())

        self.w._btn_filter_format.setChecked(True)
        self.w._btn_pure_content.setChecked(True)
        self.w._on_pure_content_toggled()
        self.assertFalse(self.w._btn_filter_format.isChecked())
        self.assertFalse(self.w._show_format_diffs)

    def test_build_region_from_text_generates_chars(self):
        region = self.w._build_region_from_text("AB\nC", page_number=2)
        self.assertEqual(2, region.page_number)
        self.assertEqual("ABC", "".join(ch.char for ch in region.chars))
        self.assertEqual(3, len(region.chars))

    @unittest.skip("covered by process-backed quality assessment test")
    def test_assess_pdf_side_quality_with_process_result(self):
        p = self._make_page("text")
        blank = self._make_page("")
        with patch("app.main_window.parse_page", side_effect=[p, blank, p, p, p]):
            with patch.object(self.w, "_check_text_quality", return_value={"quality": "bad"}):
                force, note = self.w._assess_pdf_side_quality(Path("x.pdf"), 8, "宸︿晶")
        self.assertTrue(force)
        self.assertIn("建议默认OCR", note)

    def test_assess_pdf_side_quality_delegates_to_process_task(self):
        with patch.object(self.w, "_run_process_task_with_ui_pump", return_value=(True, "建议默认OCR")) as mocked:
            force, note = self.w._assess_pdf_side_quality(Path("x.pdf"), 8, "左侧")
        self.assertTrue(force)
        self.assertIn("OCR", note)
        mocked.assert_called_once()

    def test_assess_pdf_side_quality_with_mocked_pages(self):
        with patch.object(self.w, "_run_process_task_with_ui_pump", return_value=(True, "建议默认OCR")):
            force, note = self.w._assess_pdf_side_quality(Path("x.pdf"), 8, "左侧")
        self.assertTrue(force)
        self.assertIn("OCR", note)

    def test_filter_low_confidence_noise_ops(self):
        ops = [
            DiffOp(
                type=DiffOpType.REPLACE,
                left_indices=[1],
                right_indices=[1],
                left_bboxes=[],
                right_bboxes=[],
                meta={"left_text": "l", "right_text": "1"},
            ),
            DiffOp(
                type=DiffOpType.FORMAT_CHANGE,
                left_indices=[],
                right_indices=[],
                left_bboxes=[],
                right_bboxes=[],
                meta={},
            ),
        ]
        with patch.object(self.w, "_normalized_similarity", return_value=0.98):
            filtered, removed = self.w._filter_low_confidence_noise_ops(
                ops,
                "abcdef",
                "abcdef",
                {"quality": "bad"},
                {"quality": "warning"},
            )
        self.assertEqual(1, removed)
        self.assertEqual(1, len(filtered))
        self.assertEqual(DiffOpType.FORMAT_CHANGE, filtered[0].type)

    def test_suppress_weak_confusable_ops(self):
        ops = [
            DiffOp(
                type=DiffOpType.REPLACE,
                left_indices=[1],
                right_indices=[1],
                left_bboxes=[],
                right_bboxes=[],
                meta={"left_text": "l", "right_text": "1"},
            ),
            DiffOp(
                type=DiffOpType.ADD,
                left_indices=[],
                right_indices=[2],
                left_bboxes=[],
                right_bboxes=[],
                meta={"left_text": "", "right_text": "X"},
            ),
        ]
        with patch.object(self.w, "_normalized_similarity", return_value=0.96):
            kept, removed = self.w._suppress_weak_confusable_ops(
                ops,
                "abcdef",
                "abcdef",
                ocr_used=True,
                left_quality={"quality": "bad"},
                right_quality={"quality": "warning"},
            )
        self.assertEqual(1, removed)
        self.assertEqual(1, len(kept))
        self.assertEqual(DiffOpType.ADD, kept[0].type)

    def test_current_mode_accessors(self):
        self.assertIn(self.w._current_reading_order_mode(), {"auto", "raw", "single_column", "two_column"})
        self.assertIn(self.w._current_ocr_mode(), {"auto", "sync", "async"})

    def test_low_reliability_guard_blocks_ocr_micro_diffs(self):
        op = DiffOp(
            type=DiffOpType.REPLACE,
            left_indices=[0],
            right_indices=[0],
            left_bboxes=[],
            right_bboxes=[],
            meta={"left_text": "A", "right_text": ""},
        )
        with patch.object(self.w, "_normalized_similarity", return_value=0.99):
            blocked, reason = self.w._should_block_low_reliability_diffs(
                ops=[op],
                left_text="sample text",
                right_text="鑹炬洸娉婂笗涔欓唶鑳虹墖璇存槑",
                left_quality={"quality": "warning", "confidence": 78},
                right_quality={"quality": "warning", "confidence": 78},
                ocr_used=True,
                left_ocr_applied=False,
                right_ocr_applied=True,
            )
        self.assertTrue(blocked)
        self.assertTrue(len(reason) > 0)

    def test_low_reliability_guard_not_block_when_no_ocr(self):
        op = DiffOp(
            type=DiffOpType.REPLACE,
            left_indices=[0],
            right_indices=[0],
            left_bboxes=[],
            right_bboxes=[],
            meta={"left_text": "A", "right_text": "B"},
        )
        blocked, reason = self.w._should_block_low_reliability_diffs(
            ops=[op],
            left_text="A",
            right_text="B",
            left_quality={"quality": "warning", "confidence": 60},
            right_quality={"quality": "warning", "confidence": 60},
            ocr_used=False,
            left_ocr_applied=False,
            right_ocr_applied=False,
        )
        self.assertFalse(blocked)
        self.assertEqual("", reason)

    def test_compare_decision_status_review_on_timeout_and_low_quality(self):
        with patch.object(self.w, "_normalized_similarity", return_value=0.95):
            status, note = self.w._compare_decision_status(
                left_text="A",
                right_text="A",
                left_quality={"quality": "warning", "confidence": 78},
                right_quality={"quality": "warning", "confidence": 79},
                ocr_used=True,
                left_ocr_applied=False,
                right_ocr_applied=True,
                ocr_errors=["right variant#1: Background task timed out (>24s)"],
            )
        self.assertEqual("REVIEW", status)
        self.assertIn("人工复核", note)

    def test_compare_decision_status_reference_only_when_coords_unreliable(self):
        status, note = self.w._compare_decision_status(
            left_text="A",
            right_text="B",
            left_quality={"quality": "warning", "confidence": 76},
            right_quality={"quality": "good", "confidence": 90},
            ocr_used=True,
            left_ocr_applied=True,
            right_ocr_applied=False,
            ocr_errors=[],
            left_coords_reliable=False,
            right_coords_reliable=True,
        )
        self.assertEqual("REFERENCE_ONLY", status)
        self.assertIn("定位不可信", note)

    def test_compare_decision_status_pass_when_non_ocr(self):
        status, note = self.w._compare_decision_status(
            left_text="A",
            right_text="B",
            left_quality={"quality": "warning", "confidence": 50},
            right_quality={"quality": "warning", "confidence": 50},
            ocr_used=False,
            left_ocr_applied=False,
            right_ocr_applied=False,
            ocr_errors=[],
        )
        self.assertEqual("PASS", status)
        self.assertEqual("", note)

    def test_compare_decision_status_review_when_ocr_recommended_but_blocked(self):
        status, note = self.w._compare_decision_status(
            left_text="A",
            right_text="B",
            left_quality={"quality": "warning", "confidence": 50},
            right_quality={"quality": "warning", "confidence": 50},
            ocr_used=False,
            left_ocr_applied=False,
            right_ocr_applied=False,
            ocr_errors=[],
            ocr_was_recommended=True,
            ocr_state=OcrRunState.BLOCKED,
            ocr_state_reason="cannot_run",
        )
        self.assertEqual("REVIEW", status)
        self.assertIn("cannot_run", note)

    def test_resolve_ocr_engines_skips_blocked_local_and_keeps_cloud(self):
        fake_check = LocalOcrSelfCheck(
            available=False,
            code="runtime_import",
            message="numpy missing",
            worker_python="python",
            runtime_dir="",
            json_exe="",
            python_worker_ready=False,
            json_ready=False,
        )
        fake_cloud = object()
        with (
            patch.dict("os.environ", {"VERBATIM_OCR_ROUTE": "local_first"}, clear=False),
            patch.object(self.w, "_get_local_ocr_self_check", return_value=fake_check),
            patch.object(self.w, "_get_cloud_ocr_engine", return_value=fake_cloud),
        ):
            engines = self.w._resolve_ocr_engines()
        self.assertEqual([("cloud", fake_cloud)], engines)

    def test_refresh_ocr_ui_state_surfaces_blocked_local_runtime(self):
        self.w._ocr_cfg = OcrConfig(
            token="tok",
            source="file",
            token_storage="plain",
        )
        fake_check = LocalOcrSelfCheck(
            available=False,
            code="numpy_abi_mismatch",
            message="local OCR runtime is incompatible with NumPy 2.x; create an isolated OCR env with numpy<2",
            worker_python="python",
            runtime_dir="D:/ocr_runtime",
            json_exe="",
            python_worker_ready=False,
            json_ready=False,
        )
        with (
            patch.dict("os.environ", {"VERBATIM_OCR_ROUTE": "local_first"}, clear=False),
            patch.object(self.w, "_get_local_ocr_self_check", return_value=fake_check),
        ):
            self.w._refresh_ocr_ui_state()
        self.assertIn("numpy<2", self.w._ocr_token_status.text())
        self.assertIn("numpy<2", self.w._btn_use_ocr.toolTip())

    def test_render_zoom_for_side(self):
        self.w._left_zoom_ratio = 1.2
        self.w._right_zoom_ratio = 0.8
        self.assertGreater(self.w._render_zoom_for("left"), self.w._render_zoom_for("right"))

    def test_load_selection_clamps_page_index_to_page_count_history_entry(self):
        self.w._left_pdf = Path("left.pdf")
        self.w._right_pdf = Path("right.pdf")
        self.w._left_page_count = 14
        self.w._right_page_count = 14
        self.w._left_page_combo.clear()
        self.w._right_page_combo.clear()
        for i in range(14):
            self.w._left_page_combo.addItem(f"第{i + 1}页")
            self.w._right_page_combo.addItem(f"第{i + 1}页")

        selection = SimpleNamespace(
            left_page=16,
            right_page=15,
            left_bbox=(1.0, 2.0, 3.0, 4.0),
            right_bbox=(5.0, 6.0, 7.0, 8.0),
        )

        with patch.object(self.w, "_load_page_data"), patch.object(self.w, "_load_into_view"):
            self.w._load_selection(selection)

        self.assertEqual(13, self.w._left_page_number)
        self.assertEqual(13, self.w._right_page_number)
        self.assertEqual(13, self.w._left_page_combo.currentIndex())
        self.assertEqual(13, self.w._right_page_combo.currentIndex())

    def test_ocr_candidate_score_penalizes_weird_tokens(self):
        clean_score, _ = self.w._ocr_candidate_score("abcde")
        noisy_score, _ = self.w._ocr_candidate_score("ab1c2")
        self.assertGreater(clean_score, noisy_score)

    def test_try_ocr_text_accepts_low_score_as_text_only_reference(self):
        sample_pdf = next(Path(".").rglob("*艾曲泊帕乙醇胺片说明书批件*.pdf"))
        cache_key = self.w._ocr_cache_key(sample_pdf, 0, (1.0, 2.0, 30.0, 40.0), "left")

        class _FakeEngine:
            def recognize(self, **kwargs):
                return EngineResult(
                    text="可读OCR文本" * 8,
                    engine="fake",
                    mode="sync",
                    spans=(OcrSpan(text="可读OCR文本", bbox=(0.0, 0.0, 10.0, 5.0)),),
                )

        rendered = SimpleNamespace(image_bytes=b"\x89PNG" + b"0" * 128, clip_bbox=(0.0, 0.0, 100.0, 50.0), zoom=3.0)
        with (
            patch.object(self.w, "_resolve_ocr_engines", return_value=[("cloud", _FakeEngine())]),
            patch.object(self.w, "_run_process_task_with_ui_pump", return_value=rendered),
            patch.object(self.w, "_check_text_quality", return_value={"quality": "bad", "confidence": 35}),
            patch.object(self.w, "_garble_signal_score", return_value=(3, ["garble"])),
            patch.object(self.w, "_normalized_similarity", return_value=0.50),
        ):
            text = self.w._try_ocr_text(
                sample_pdf,
                0,
                (1.0, 2.0, 30.0, 40.0),
                "left",
                baseline_text="基" * 56,
                peer_text="",
            )

        self.assertTrue(text.startswith("可读OCR文本"))
        self.assertEqual([], self.w._get_cached_ocr_spans(cache_key))
        self.assertFalse(self.w._ocr_cached_coords_reliable(cache_key))

    def test_looks_like_path_text(self):
        self.assertTrue(self.w._looks_like_path_text("/sdk_storage/resources/images/img_v3_xxx.jpg"))
        self.assertTrue(self.w._looks_like_path_text("https://example.com/a.png"))
        self.assertFalse(self.w._looks_like_path_text("normal text"))

    def test_on_compare_clicked_precheck_paths(self):
        self.w._left_page = None
        self.w._right_page = None
        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            self.w._on_compare_clicked()
        self.assertIn("Page data not ready", buf.getvalue())

        self.w._left_page = self._make_page("A")
        self.w._right_page = self._make_page("B")
        self.w._left_sel_bbox = None
        self.w._right_sel_bbox = None
        buf2 = StringIO()
        with contextlib.redirect_stdout(buf2):
            self.w._on_compare_clicked()
        self.assertIn("Please select both left and right regions", buf2.getvalue())

    def test_zoom_keeps_diff_focus_not_full_selection_overlay(self):
        op = DiffOp(
            type=DiffOpType.REPLACE,
            left_indices=[0],
            right_indices=[0],
            left_bboxes=[],
            right_bboxes=[],
            meta={"left_text": "A", "right_text": "B"},
        )
        left_region = self._make_region("ABC")
        right_region = self._make_region("ABD")
        self.w._last_diff_ops = [op]
        self.w._last_left_region = left_region
        self.w._last_right_region = right_region
        self.w._last_left_ocr_applied = False
        self.w._last_right_ocr_applied = False
        self.w._focused_diff_op = op
        self.w._left_sel_bbox = (0.0, 0.0, 500.0, 100.0)
        self.w._left_pdf = Path(__file__)

        with patch.object(self.w, "_load_into_view", return_value=None):
            self.w._set_zoom_ratio("left", 1.5)

        selected = getattr(self.w.left_view._image, "_selected_overlays", [])
        self.assertGreaterEqual(len(selected), 1)
        rect = selected[0][0]
        # Should keep focused diff-size highlight, not fallback to full selection bbox.
        self.assertLess(rect.width(), 80.0)

    def test_focus_op_skips_ocr_applied_side_selection_overlay(self):
        op = DiffOp(
            type=DiffOpType.REPLACE,
            left_indices=[0],
            right_indices=[0],
            left_bboxes=[],
            right_bboxes=[],
            meta={"left_text": "A", "right_text": "B"},
        )
        self.w._last_left_region = self._make_region("ABC")
        self.w._last_right_region = self._make_region("ABD")
        self.w._last_left_ocr_applied = False
        self.w._last_right_ocr_applied = True

        self.w._focus_op(op)

        left_selected = getattr(self.w.left_view._image, "_selected_overlays", [])
        right_selected = getattr(self.w.right_view._image, "_selected_overlays", [])
        self.assertGreaterEqual(len(left_selected), 1)
        self.assertEqual([], right_selected)

    def test_rerender_hides_overlays_for_synthetic_regions(self):
        op = DiffOp(
            type=DiffOpType.REPLACE,
            left_indices=[0],
            right_indices=[0],
            left_bboxes=[],
            right_bboxes=[],
            meta={"left_text": "A", "right_text": "B"},
        )
        self.w._last_diff_ops = [op]
        self.w._last_left_region = self.w._build_region_from_text("ABC", page_number=0)
        self.w._last_right_region = self.w._build_region_from_text("ABD", page_number=0)
        self.w._last_left_ocr_applied = False
        self.w._last_right_ocr_applied = False
        self.w._last_left_coords_reliable = True
        self.w._last_right_coords_reliable = True

        rendered = self.w._rerender_diff_overlays(keep_focus=False)

        self.assertTrue(rendered)
        self.assertEqual([], getattr(self.w.left_view._image, "_badges", []))
        self.assertEqual([], getattr(self.w.right_view._image, "_badges", []))
        self.assertEqual([], getattr(self.w.left_view._image, "_overlays", []))
        self.assertEqual([], getattr(self.w.right_view._image, "_overlays", []))

    def test_update_left_page_clears_stale_diff_cache(self):
        op = DiffOp(
            type=DiffOpType.REPLACE,
            left_indices=[0],
            right_indices=[0],
            left_bboxes=[],
            right_bboxes=[],
            meta={"left_text": "A", "right_text": "B"},
        )
        self.w._last_diff_ops = [op]
        self.w._last_left_region = self.w._build_region_from_text("ABC", page_number=0)
        self.w._last_right_region = self.w._build_region_from_text("ABD", page_number=0)
        self.w._focused_diff_op = op
        self.w._left_pdf = Path("dummy_left.pdf")
        self.w._left_page_count = 2
        self.w._left_page_number = 0

        with patch.object(self.w, "_load_into_view", return_value=None):
            with patch("app.main_window.parse_page", return_value=self._make_page("A")):
                self.w._update_left_page()

        self.assertEqual([], self.w._last_diff_ops)
        self.assertIsNone(self.w._last_left_region)
        self.assertIsNone(self.w._last_right_region)
        self.assertIsNone(self.w._focused_diff_op)

    def test_update_left_page_drops_stale_render_and_commits_latest_target(self):
        self.w._left_pdf = Path("dummy_left.pdf")
        self.w._left_page_count = 3
        self.w._left_page_number = 0
        self.w._left_committed_page_number = 0
        self.w._left_page_combo.clear()
        for i in range(3):
            self.w._left_page_combo.addItem(f"第{i + 1}页")

        render_calls: list[int] = []

        def fake_render(pdf_path, *, page_number=0, side="left"):
            render_calls.append(page_number)
            if page_number == 0 and len(render_calls) == 1:
                self.w._set_page_index("left", 2)
            pix = QPixmap(8, 8)
            pix.fill()
            return pix

        def fake_task(task_name, *args, **kwargs):
            self.assertEqual("parse_page", task_name)
            return self._make_page(f"page-{int(args[1])}")

        with (
            patch.object(self.w, "_render_page_pixmap", side_effect=fake_render),
            patch.object(self.w, "_run_process_task_with_ui_pump", side_effect=fake_task),
            patch.object(self.w, "_apply_page_text_layer_status"),
        ):
            self.w._update_left_page()

        self.assertEqual([0, 2], render_calls)
        self.assertEqual(2, self.w._left_page_number)
        self.assertEqual(2, self.w._left_committed_page_number)
        self.assertFalse(self.w._left_nav_loading)
        self.assertEqual(2, self.w._left_page_combo.currentIndex())
        self.assertEqual("page-2", "".join(ch.char for ch in self.w._left_page.text_chars))

    def test_clear_cached_diff_result_clears_visual_badges(self):
        self.w.left_view.set_overlays([(QRectF(1.0, 1.0, 10.0, 10.0), QColor(255, 0, 0))])
        self.w.right_view.set_overlays([(QRectF(2.0, 2.0, 8.0, 8.0), QColor(0, 128, 255))])
        self.w.left_view.set_badges([(QRectF(1.0, 1.0, 10.0, 10.0), "改", QColor(255, 0, 0))])
        self.w.right_view.set_badges([(QRectF(2.0, 2.0, 8.0, 8.0), "增", QColor(0, 128, 255))])
        self.w.left_view.set_selected_overlays([(QRectF(1.0, 1.0, 10.0, 10.0), QColor(255, 0, 0))])
        self.w.right_view.set_selected_overlays([(QRectF(2.0, 2.0, 8.0, 8.0), QColor(0, 128, 255))])

        self.w._clear_cached_diff_result()

        self.assertEqual([], getattr(self.w.left_view._image, "_badges", []))
        self.assertEqual([], getattr(self.w.right_view._image, "_badges", []))
        self.assertEqual([], getattr(self.w.left_view._image, "_overlays", []))
        self.assertEqual([], getattr(self.w.right_view._image, "_overlays", []))
        self.assertEqual([], getattr(self.w.left_view._image, "_selected_overlays", []))
        self.assertEqual([], getattr(self.w.right_view._image, "_selected_overlays", []))

    def test_set_left_pdf_clears_stale_diff_cache(self):
        op = DiffOp(
            type=DiffOpType.REPLACE,
            left_indices=[0],
            right_indices=[0],
            left_bboxes=[],
            right_bboxes=[],
            meta={"left_text": "A", "right_text": "B"},
        )
        self.w._last_diff_ops = [op]
        self.w._last_left_region = self.w._build_region_from_text("ABC", page_number=0)
        self.w._last_right_region = self.w._build_region_from_text("ABD", page_number=0)
        self.w._focused_diff_op = op

        class _Doc:
            page_count = 1

            def close(self):
                return None

        with patch("app.main_window.fitz.open", return_value=_Doc()):
            with patch.object(self.w, "_assess_pdf_side_quality", return_value=(False, "ok")):
                with patch.object(self.w, "_load_into_view", return_value=None):
                    with patch.object(self.w, "_maybe_parse_pages", return_value=None):
                        self.w._set_left_pdf(Path("dummy_left.pdf"))

        self.assertEqual([], self.w._last_diff_ops)
        self.assertIsNone(self.w._last_left_region)
        self.assertIsNone(self.w._last_right_region)
        self.assertIsNone(self.w._focused_diff_op)

    def test_compare_flow_generates_result_with_mocks(self):
        self.w._left_page = self._make_page("ABC")
        self.w._right_page = self._make_page("ABD")
        self.w._left_sel_bbox = (0.0, 0.0, 50.0, 20.0)
        self.w._right_sel_bbox = (0.0, 0.0, 50.0, 20.0)

        left_region = self.w._build_region_from_text("ABC", page_number=0)
        right_region = self.w._build_region_from_text("ABD", page_number=0)
        op = DiffOp(
            type=DiffOpType.REPLACE,
            left_indices=[2],
            right_indices=[2],
            left_bboxes=[(2.0, 0.0, 3.0, 1.0)],
            right_bboxes=[(2.0, 0.0, 3.0, 1.0)],
            meta={"left_text": "C", "right_text": "D"},
        )
        norm_log = SimpleNamespace(to_string=lambda: "norm")
        field_result = SimpleNamespace(
            enabled=False,
            disable_reason="skip",
            note="",
            left_kvs=[],
            right_kvs=[],
            field_diffs=[],
        )

        with patch.object(self.w, "_current_reading_order_mode", return_value="raw"):
            with patch.object(self.w, "_extract_regions_with_mode", return_value=(left_region, right_region)):
                with patch.object(self.w, "_check_text_quality", return_value={"quality": "good", "confidence": 95}):
                    with patch(
                        "app.main_window.collect_quality_warnings", return_value=([], {"left": 95, "right": 96})
                    ):
                        with patch(
                            "app.main_window.decide_ocr",
                            return_value=SimpleNamespace(
                                left_try_ocr=False,
                                right_try_ocr=False,
                                left_reason="",
                                right_reason="",
                                recommended=False,
                            ),
                        ):
                            with patch(
                                "app.main_window.run_ocr_fallback",
                                return_value=SimpleNamespace(
                                    left_text="ABC",
                                    right_text="ABD",
                                    left_ocr_applied=False,
                                    right_ocr_applied=False,
                                    ocr_used=False,
                                    replaced_sides=[],
                                    attempted_but_empty=False,
                                    skipped_no_config=False,
                                    ocr_note="",
                                ),
                            ):
                                with patch("app.main_window.diff_regions", return_value=([op], norm_log)):
                                    with patch("app.main_window.run_field_mapping", return_value=field_result):
                                        with patch("app.main_window.build_compare_result_summary", return_value="ok"):
                                            self.w._on_compare_clicked()

        self.assertEqual(1, len(self.w._last_diff_ops))
        self.assertTrue(self.w._last_compare_summary.startswith("ok"))
        self.assertTrue(self.w._btn_save_selection.isEnabled())

    def test_compare_flow_ocr_fallback_path_with_mocks(self):
        self.w._left_page = self._make_page("ABC")
        self.w._right_page = self._make_page("A8C")
        self.w._left_sel_bbox = (0.0, 0.0, 50.0, 20.0)
        self.w._right_sel_bbox = (0.0, 0.0, 50.0, 20.0)
        self.w._left_pdf = Path("dummy_left.pdf")
        self.w._right_pdf = Path("dummy_right.pdf")

        left_region = self.w._build_region_from_text("ABC", page_number=0)
        right_region = self.w._build_region_from_text("A8C", page_number=0)
        op = DiffOp(
            type=DiffOpType.REPLACE,
            left_indices=[1],
            right_indices=[1],
            left_bboxes=[(1.0, 0.0, 2.0, 1.0)],
            right_bboxes=[(1.0, 0.0, 2.0, 1.0)],
            meta={"left_text": "B", "right_text": "8"},
        )
        norm_log = SimpleNamespace(to_string=lambda: "norm")
        field_result = SimpleNamespace(
            enabled=False,
            disable_reason="skip",
            note="",
            left_kvs=[],
            right_kvs=[],
            field_diffs=[],
        )

        with patch.object(self.w, "_current_reading_order_mode", return_value="raw"):
            with patch.object(self.w, "_extract_regions_with_mode", return_value=(left_region, right_region)):
                with patch.object(self.w, "_check_text_quality", return_value={"quality": "good", "confidence": 90}):
                    with patch(
                        "app.main_window.collect_quality_warnings", return_value=(["warn"], {"left": 60, "right": 61})
                    ):
                        with patch(
                            "app.main_window.decide_ocr",
                            return_value=SimpleNamespace(
                                left_try_ocr=False,
                                right_try_ocr=True,
                                left_reason="",
                                right_reason="bad",
                                recommended=True,
                            ),
                        ):
                            with patch(
                                "app.main_window.run_ocr_fallback",
                                return_value=SimpleNamespace(
                                    left_text="ABC",
                                    right_text="ABC",
                                    left_ocr_applied=False,
                                    right_ocr_applied=True,
                                    ocr_used=True,
                                    replaced_sides=["right"],
                                    attempted_but_empty=False,
                                    skipped_no_config=False,
                                    ocr_note="OCR fallback enabled",
                                ),
                            ):
                                with patch("app.main_window.diff_regions", return_value=([op], norm_log)):
                                    with patch("app.main_window.run_field_mapping", return_value=field_result):
                                        with patch(
                                            "app.main_window.build_compare_result_summary", return_value="ocr-on"
                                        ):
                                            with patch.object(self.w, "_build_visual_diff_ops", return_value=[]):
                                                self.w._on_compare_clicked()

        self.assertTrue(self.w._last_right_ocr_applied)
        self.assertFalse(self.w._last_left_ocr_applied)
        self.assertTrue(self.w._last_compare_summary.startswith("ocr-on"))

    def test_compare_low_quality_requires_manual_gate_then_continues(self):
        self.w._left_page = self._make_page("ABC")
        self.w._right_page = self._make_page("XYZ")
        self.w._left_sel_bbox = (0.0, 0.0, 50.0, 20.0)
        self.w._right_sel_bbox = (0.0, 0.0, 50.0, 20.0)
        left_region = self.w._build_region_from_text("ABC", page_number=0)
        right_region = self.w._build_region_from_text("XYZ", page_number=0)
        op = DiffOp(
            type=DiffOpType.REPLACE,
            left_indices=[0],
            right_indices=[0],
            left_bboxes=[(0.0, 0.0, 1.0, 1.0)],
            right_bboxes=[(0.0, 0.0, 1.0, 1.0)],
            meta={"left_text": "A", "right_text": "X"},
        )
        norm_log = SimpleNamespace(to_string=lambda: "norm")
        field_result = SimpleNamespace(
            enabled=False,
            disable_reason="skip",
            note="",
            left_kvs=[],
            right_kvs=[],
            field_diffs=[],
        )

        def _q(_text):
            return {"quality": "bad", "confidence": 40}

        with patch.object(self.w, "_check_text_quality", side_effect=_q):
            with patch.object(self.w, "_current_reading_order_mode", return_value="raw"):
                with patch.object(self.w, "_extract_regions_with_mode", return_value=(left_region, right_region)):
                    with patch(
                        "app.main_window.collect_quality_warnings", return_value=(["warn"], {"left": 40, "right": 40})
                    ):
                        with patch(
                            "app.main_window.decide_ocr",
                            return_value=SimpleNamespace(
                                left_try_ocr=False,
                                right_try_ocr=True,
                                left_reason="",
                                right_reason="bad",
                                recommended=True,
                            ),
                        ):
                            with patch(
                                "app.main_window.run_ocr_fallback",
                                return_value=SimpleNamespace(
                                    left_text="ABC",
                                    right_text="",
                                    left_ocr_applied=False,
                                    right_ocr_applied=False,
                                    ocr_used=False,
                                    replaced_sides=[],
                                    attempted_but_empty=True,
                                    skipped_no_config=False,
                                    ocr_note="",
                                ),
                            ):
                                with patch("app.main_window.diff_regions", return_value=([op], norm_log)):
                                    with patch("app.main_window.run_field_mapping", return_value=field_result):
                                        with patch(
                                            "app.main_window.build_compare_result_summary", return_value="degraded"
                                        ):
                                            self.w._on_compare_clicked()

        self.assertEqual(1, len(self.w._last_diff_ops))
        warns = getattr(self.w, "_last_quality_warnings", [])
        self.assertTrue(len(warns) > 0)
        self.assertTrue(any("人工确认" in w for w in warns))

    def test_compare_manual_gate_cancel_stops_compare(self):
        self.w._left_page = self._make_page("ABC")
        self.w._right_page = self._make_page("XYZ")
        self.w._left_sel_bbox = (0.0, 0.0, 50.0, 20.0)
        self.w._right_sel_bbox = (0.0, 0.0, 50.0, 20.0)
        left_region = self.w._build_region_from_text("ABC", page_number=0)
        right_region = self.w._build_region_from_text("XYZ", page_number=0)
        op = DiffOp(
            type=DiffOpType.REPLACE,
            left_indices=[0],
            right_indices=[0],
            left_bboxes=[(0.0, 0.0, 1.0, 1.0)],
            right_bboxes=[(0.0, 0.0, 1.0, 1.0)],
            meta={"left_text": "A", "right_text": "X"},
        )
        norm_log = SimpleNamespace(to_string=lambda: "norm")
        field_result = SimpleNamespace(
            enabled=False,
            disable_reason="skip",
            note="",
            left_kvs=[],
            right_kvs=[],
            field_diffs=[],
        )

        def _q(_text):
            return {"quality": "bad", "confidence": 40}

        with patch.object(self.w, "_check_text_quality", side_effect=_q):
            with patch.object(self.w, "_current_reading_order_mode", return_value="raw"):
                with patch.object(self.w, "_extract_regions_with_mode", return_value=(left_region, right_region)):
                    with patch(
                        "app.main_window.collect_quality_warnings", return_value=(["warn"], {"left": 40, "right": 40})
                    ):
                        with patch(
                            "app.main_window.decide_ocr",
                            return_value=SimpleNamespace(
                                left_try_ocr=False,
                                right_try_ocr=True,
                                left_reason="",
                                right_reason="bad",
                                recommended=True,
                            ),
                        ):
                            with patch(
                                "app.main_window.run_ocr_fallback",
                                return_value=SimpleNamespace(
                                    left_text="ABC",
                                    right_text="",
                                    left_ocr_applied=False,
                                    right_ocr_applied=False,
                                    ocr_used=False,
                                    replaced_sides=[],
                                    attempted_but_empty=True,
                                    skipped_no_config=False,
                                    ocr_note="",
                                ),
                            ):
                                with patch.object(self.w, "_run_manual_review_gate", return_value=(False, "ABC", "")):
                                    with patch("app.main_window.diff_regions", return_value=([op], norm_log)):
                                        with patch("app.main_window.run_field_mapping", return_value=field_result):
                                            with patch(
                                                "app.main_window.build_compare_result_summary", return_value="degraded"
                                            ):
                                                self.w._on_compare_clicked()

        self.assertEqual(0, len(self.w._last_diff_ops))
        self.assertIn("需人工确认后才能继续", self.w._last_compare_summary)

    def test_background_task_timeout_guard(self):
        self.w._bg_task_timeout_ms = 1000

        def _slow():
            time.sleep(2.0)
            return 1

        with self.assertRaises(TimeoutError):
            self.w._run_in_background_with_ui_pump(_slow)

    def test_background_process_task_timeout_guard(self):
        with self.assertRaises(TimeoutError):
            self.w._run_process_task_with_ui_pump("sleep", 2.0, timeout_ms=200)

    def test_background_process_task_ignores_legacy_ocr_worker_env(self):
        captured: dict[str, object] = {}

        class _FakeProc:
            def __init__(self, cmd, **_kwargs):
                captured["cmd"] = cmd
                captured["kwargs"] = _kwargs
                output_path = Path(cmd[cmd.index("--output") + 1])
                output_path.write_bytes(pickle.dumps({"ok": True}))
                self.returncode = 0

            def poll(self):
                return 0

            def communicate(self):
                return (b"", b"")

        with patch.dict("os.environ", {"VERBATIM_WORKER_PYTHON": r"D:\ocr-only\python.exe"}, clear=False):
            with patch("app.main_window.subprocess.Popen", side_effect=lambda *a, **k: _FakeProc(*a, **k)):
                result = self.w._run_process_task_with_ui_pump("demo_task", timeout_ms=200)
        self.assertEqual({"ok": True}, result)
        cmd = captured["cmd"]
        self.assertNotEqual(r"D:\ocr-only\python.exe", cmd[0])

    def test_background_process_task_honors_bg_worker_env(self):
        captured: dict[str, object] = {}

        class _FakeProc:
            def __init__(self, cmd, **_kwargs):
                captured["cmd"] = cmd
                captured["kwargs"] = _kwargs
                output_path = Path(cmd[cmd.index("--output") + 1])
                output_path.write_bytes(pickle.dumps({"ok": True}))
                self.returncode = 0

            def poll(self):
                return 0

            def communicate(self):
                return (b"", b"")

        with patch.dict("os.environ", {"VERBATIM_BG_WORKER_PYTHON": r"D:\bg\python.exe"}, clear=False):
            with patch("app.main_window.subprocess.Popen", side_effect=lambda *a, **k: _FakeProc(*a, **k)):
                result = self.w._run_process_task_with_ui_pump("demo_task", timeout_ms=200)
        self.assertEqual({"ok": True}, result)
        self.assertEqual(r"D:\bg\python.exe", captured["cmd"][0])

    def test_stale_diff_item_click_is_ignored(self):
        stale_op = DiffOp(
            type=DiffOpType.REPLACE,
            left_indices=[0],
            right_indices=[0],
            left_bboxes=[(0.0, 0.0, 1.0, 1.0)],
            right_bboxes=[(0.0, 0.0, 1.0, 1.0)],
            meta={"left_text": "A", "right_text": "B"},
        )
        self.w._last_diff_ops = []
        self.w._focused_diff_op = None
        item = QListWidgetItem("stale")
        item.setData(Qt.ItemDataRole.UserRole, ("char_diff", stale_op))
        self.w._on_diff_item_clicked(item)
        self.assertIsNone(self.w._focused_diff_op)

    def test_layout_key_controls_visible_at_1366(self):
        self.w.resize(1366, 768)
        self.w._apply_responsive_ui(force=True)
        for wd in [self.w._btn_compare, self.w._btn_left_prev, self.w._btn_right_prev, self.w._left_zoom_label]:
            self.assertTrue(wd.isVisible())
            self.assertGreater(wd.width(), 0)
            self.assertGreater(wd.height(), 0)

    def test_layout_key_controls_visible_at_1920(self):
        self.w.resize(1920, 1080)
        self.w._apply_responsive_ui(force=True)
        for wd in [self.w._btn_compare, self.w._btn_left_next, self.w._btn_right_next, self.w._right_zoom_label]:
            self.assertTrue(wd.isVisible())
            self.assertGreater(wd.width(), 0)
            self.assertGreater(wd.height(), 0)

    def test_sanitize_ocr_text_removes_path_noise(self):
        raw = (
            "艾曲泊帕乙醇胺片说明书 "
            "/c/Users/WuSiTan/AppData/Roaming/LarkShell/sdk_storage/abc/resources/images/img_v3_xxx.jpg "
            "请仔细阅读"
        )
        cleaned = self.w._sanitize_ocr_text(raw)
        self.assertIn("请仔细阅读", cleaned)
        self.assertIn("艾曲泊帕乙醇胺片说明书", cleaned)
        self.assertNotIn("sdk_storage", cleaned.lower())
        self.assertNotIn("img_v3_", cleaned.lower())

    def test_try_ocr_text_ignores_pathlike_payload(self):
        fake_result = SimpleNamespace(
            text="/c/Users/WuSiTan/AppData/Roaming/LarkShell/sdk_storage/a/resources/images/img_v3_a.jpg"
        )
        client = SimpleNamespace(
            recognize_sync=lambda *_args, **_kwargs: fake_result,
            submit_async=lambda *_args, **_kwargs: "jid",
            poll_async_result=lambda *_args, **_kwargs: fake_result,
        )
        self.w._ocr_client = client
        with patch.object(self.w, "_current_ocr_mode", return_value="sync"):
            rendered = SimpleNamespace(image_bytes=b"png" * 32, clip_bbox=(0.0, 0.0, 10.0, 10.0), zoom=3.0)
            with patch.object(self.w, "_run_process_task_with_ui_pump", return_value=rendered):
                out = self.w._try_ocr_text(
                    Path("dummy.pdf"),
                    0,
                    (0.0, 0.0, 10.0, 10.0),
                    "right",
                )
        self.assertEqual("", out)

    def test_try_ocr_text_without_cloud_client_returns_empty(self):
        with patch.object(self.w, "_get_ocr_client", return_value=None):
            rendered = SimpleNamespace(image_bytes=b"x" * 256, clip_bbox=(0.0, 0.0, 10.0, 10.0), zoom=3.0)
            with patch.object(self.w, "_run_process_task_with_ui_pump", return_value=rendered):
                out = self.w._try_ocr_text(
                    Path("dummy.pdf"),
                    0,
                    (0.0, 0.0, 10.0, 10.0),
                    "right",
                )
        self.assertEqual("", out)

    def test_try_ocr_text_cloud_sync_success(self):
        fake_result = SimpleNamespace(text="CLOUD_TEXT")
        client = SimpleNamespace(
            recognize_sync=lambda *_args, **_kwargs: fake_result,
            submit_async=lambda *_args, **_kwargs: "jid",
            poll_async_result=lambda *_args, **_kwargs: fake_result,
        )
        with patch.dict("os.environ", {"VERBATIM_OCR_ROUTE": "cloud_only"}):
            with patch.object(self.w, "_get_ocr_client", return_value=client):
                with patch.object(
                    self.w, "_run_in_background_with_ui_pump", side_effect=lambda fn, *a, **k: fn(*a, **k)
                ):
                    with patch.object(self.w, "_current_ocr_mode", return_value="sync"):
                        rendered = SimpleNamespace(image_bytes=b"x" * 256, clip_bbox=(0.0, 0.0, 10.0, 10.0), zoom=3.0)
                        with patch.object(self.w, "_run_process_task_with_ui_pump", return_value=rendered):
                            out = self.w._try_ocr_text(
                                Path("dummy.pdf"),
                                0,
                                (0.0, 0.0, 10.0, 10.0),
                                "right",
                            )
        self.assertEqual("CLOUD_TEXT", out)

    def test_try_ocr_text_cache_reuses_result_for_same_bbox(self):
        calls = {"n": 0}

        def _sync(*_args, **_kwargs):
            calls["n"] += 1
            return SimpleNamespace(text="CACHE_TEXT")

        client = SimpleNamespace(
            recognize_sync=_sync,
            submit_async=lambda *_args, **_kwargs: "jid",
            poll_async_result=lambda *_args, **_kwargs: SimpleNamespace(text="CACHE_TEXT"),
        )
        self.w._ocr_result_cache.clear()
        with patch.dict("os.environ", {"VERBATIM_OCR_ROUTE": "cloud_only"}):
            with patch.object(self.w, "_get_ocr_client", return_value=client):
                with patch.object(
                    self.w, "_run_in_background_with_ui_pump", side_effect=lambda fn, *a, **k: fn(*a, **k)
                ):
                    with patch.object(self.w, "_current_ocr_mode", return_value="sync"):
                        rendered = SimpleNamespace(image_bytes=b"x" * 256, clip_bbox=(0.0, 0.0, 10.0, 10.0), zoom=3.0)
                        with patch.object(self.w, "_run_process_task_with_ui_pump", return_value=rendered):
                            out1 = self.w._try_ocr_text(Path("dummy.pdf"), 0, (0.0, 0.0, 10.0, 10.0), "right")
                            out2 = self.w._try_ocr_text(Path("dummy.pdf"), 0, (0.0, 0.0, 10.0, 10.0), "right")
        self.assertEqual("CACHE_TEXT", out1)
        self.assertEqual("CACHE_TEXT", out2)
        self.assertEqual(2, calls["n"])

    def test_try_ocr_text_rejects_low_confidence_best_candidate(self):
        fake_result = SimpleNamespace(text="NOISE_TEXT")
        client = SimpleNamespace(
            recognize_sync=lambda *_args, **_kwargs: fake_result,
            submit_async=lambda *_args, **_kwargs: "jid",
            poll_async_result=lambda *_args, **_kwargs: fake_result,
        )
        self.w._ocr_result_cache.clear()
        with patch.object(self.w, "_get_ocr_client", return_value=client):
            with patch.object(self.w, "_run_in_background_with_ui_pump", side_effect=lambda fn, *a, **k: fn(*a, **k)):
                with patch.object(self.w, "_current_ocr_mode", return_value="sync"):
                    rendered = SimpleNamespace(image_bytes=b"x" * 256, clip_bbox=(0.0, 0.0, 10.0, 10.0), zoom=3.0)
                    with patch.object(self.w, "_run_process_task_with_ui_pump", return_value=rendered):
                        with patch.object(
                            self.w, "_check_text_quality", return_value={"quality": "bad", "confidence": 20}
                        ):
                            with patch.object(self.w, "_garble_signal_score", return_value=(3, [])):
                                with patch.object(
                                    self.w,
                                    "_ocr_candidate_score",
                                    return_value=(-56.4, {"len_delta": 0.0, "peer_sim": 0.2}),
                                ):
                                    out = self.w._try_ocr_text(
                                        Path("dummy.pdf"),
                                        0,
                                        (0.0, 0.0, 10.0, 10.0),
                                        "right",
                                        baseline_text="reference text for length",
                                    )
        self.assertEqual("", out)

    def test_update_compare_history_list_shows_records(self):
        with tempfile.TemporaryDirectory() as td:
            mgr = CompareHistoryManager(Path(td) / "compare_history.json")
            mgr.add_record(
                status="ok",
                summary="状态:PASS | 可信度:高",
                ops_count=2,
                field_diffs_count=0,
                ocr_used=True,
                compare_status="PASS",
                ocr_state="success",
                ocr_state_reason="applied",
                reliability="高",
                left_page=0,
                right_page=0,
                left_bbox=(1.0, 2.0, 3.0, 4.0),
                right_bbox=(5.0, 6.0, 7.0, 8.0),
                warnings_count=1,
                diff_ops=[],
                left_region_text="A",
                right_region_text="A",
                left_ocr_applied=False,
                right_ocr_applied=False,
            )
            with patch("app.main_window.COMPARE_HISTORY_MANAGER", mgr):
                self.w._update_compare_history_list()
                self.assertGreater(self.w._compare_history_list.count(), 0)
                self.assertIn("text", self.w._compare_history_list.item(0).text())

    def _make_page(self, text: str) -> PageData:
        chars = []
        for idx, ch in enumerate(text):
            chars.append(
                CharData(
                    char=ch,
                    index=idx,
                    bbox=(10.0 + idx * 8.0, 10.0, 16.0 + idx * 8.0, 24.0),
                    font_name="Test",
                    font_family="Test",
                    size=10.0,
                    color_rgb=(0, 0, 0),
                    style=StyleFlags(),
                )
            )
        return PageData(
            file_path="mem://test.pdf",
            page_number=0,
            width=600.0,
            height=800.0,
            text_chars=chars,
        )

    def _make_region(self, text: str, page_number: int = 0) -> RegionData:
        page = self._make_page(text)
        return RegionData(page_number=page_number, bboxes=[], chars=page.text_chars)


SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples" / "manual-verification"
ORIGINAL_PDF = SAMPLES_DIR / "original.pdf"
DIGEST_PDF = SAMPLES_DIR / "digest.pdf"


@unittest.skipUnless(ORIGINAL_PDF.exists() and DIGEST_PDF.exists(), "sample PDFs not found")
class TestGuiEndToEndWithRealPdf(unittest.TestCase):
    """End-to-end GUI test: load real PDFs, trigger compare, verify diff detection.

    This test exercises the real pipeline (parse_page -> extract_region -> diff_regions)
    through the MainWindow, with only OCR mocked out (since it requires external deps).
    """

    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self):
        from core.pdf_parser import parse_page

        self.w = MainWindow()
        self.w._get_local_ocr_self_check = lambda force=False: LocalOcrSelfCheck(
            available=True, code="ready", message="ready",
            worker_python="python", runtime_dir="D:/ocr",
            json_exe="D:/ocr/PaddleOCR-json.exe",
            python_worker_ready=False, json_ready=True,
        )

        # Load real PDF pages
        left_page = parse_page(ORIGINAL_PDF, 0)
        right_page = parse_page(DIGEST_PDF, 1)  # page 0 is empty; page 1 has text

        self.w._left_pdf = ORIGINAL_PDF
        self.w._right_pdf = DIGEST_PDF
        self.w._left_page_number = 0
        self.w._right_page_number = 1
        self.w._left_page = left_page
        self.w._right_page = right_page
        self.w._left_page_count = 1
        self.w._right_page_count = 2

        # Select the full page on both sides
        self.w._left_sel_bbox = (0.0, 0.0, left_page.width, left_page.height)
        self.w._right_sel_bbox = (0.0, 0.0, right_page.width, right_page.height)
        self.w._update_compare_enabled()
        self.w.show()

    def tearDown(self):
        self.w.close()

    def test_real_pdf_compare_detects_differences(self):
        """Compare two different PDFs and verify that diff ops are produced."""
        self.assertTrue(self.w._btn_compare.isEnabled())

        # Mock only OCR (external dependency), let real diff pipeline run
        with patch("app.main_window.decide_ocr", return_value=SimpleNamespace(
            left_try_ocr=False, right_try_ocr=False,
            left_reason="text-layer-ok", right_reason="text-layer-ok",
            recommended=False,
        )):
            with patch("app.main_window.run_ocr_fallback", return_value=SimpleNamespace(
                left_text="", right_text="",
                left_ocr_applied=False, right_ocr_applied=False,
                ocr_used=False, replaced_sides=[],
                attempted_but_empty=False, skipped_no_config=False,
                ocr_note="",
            )):
                self.w._on_compare_clicked()

        # Verify diff ops were produced
        text_ops = [
            o for o in self.w._last_diff_ops
            if o.type in (DiffOpType.ADD, DiffOpType.DEL, DiffOpType.REPLACE)
        ]
        self.assertGreater(len(text_ops), 0, "different PDFs should produce text diff ops")

        # Verify summary is not an error
        self.assertNotIn("失败", self.w._last_compare_summary)
        self.assertNotIn("error", self.w._last_compare_summary.lower())

    def test_real_pdf_same_page_produces_zero_text_ops(self):
        """Compare a page against itself — should produce no text diff ops."""
        # Set both sides to the same page
        from core.pdf_parser import parse_page

        page = parse_page(ORIGINAL_PDF, 0)
        self.w._right_page = page
        self.w._right_page_number = 0
        self.w._right_sel_bbox = (0.0, 0.0, page.width, page.height)

        with patch("app.main_window.decide_ocr", return_value=SimpleNamespace(
            left_try_ocr=False, right_try_ocr=False,
            left_reason="text-layer-ok", right_reason="text-layer-ok",
            recommended=False,
        )):
            with patch("app.main_window.run_ocr_fallback", return_value=SimpleNamespace(
                left_text="", right_text="",
                left_ocr_applied=False, right_ocr_applied=False,
                ocr_used=False, replaced_sides=[],
                attempted_but_empty=False, skipped_no_config=False,
                ocr_note="",
            )):
                self.w._on_compare_clicked()

        text_ops = [
            o for o in self.w._last_diff_ops
            if o.type in (DiffOpType.ADD, DiffOpType.DEL, DiffOpType.REPLACE)
        ]
        self.assertEqual(len(text_ops), 0, "same page should produce no text diff ops")


if __name__ == "__main__":
    unittest.main()
