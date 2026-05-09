from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication, QListWidget, QTabWidget

from app.diff_presenter import _inline_char_diff_html, build_diff_details_html, build_field_diff_details_html, populate_diff_lists
from app.view_models import CompareViewModel
from core.field_mapper import FieldDiff
from core.models import DiffOp, DiffOpType


class TestDiffPresenter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._app = QApplication.instance() or QApplication([])

    def test_build_diff_details_html_for_replace(self):
        op = DiffOp(
            type=DiffOpType.REPLACE,
            left_indices=[0],
            right_indices=[0],
            left_bboxes=[],
            right_bboxes=[],
            meta={"left_text": "left text", "right_text": "right text"},
        )
        html = build_diff_details_html(op)
        self.assertIn("Content Changed", html)
        # Inline char diff wraps differing chars in <span> tags.
        self.assertIn("background:", html)
        # Unchanged suffix "text" should still appear as plain text.
        self.assertIn("text", html)

    def test_build_diff_details_html_for_visual_diff(self):
        op = DiffOp(
            type=DiffOpType.VISUAL_DIFF,
            left_indices=[],
            right_indices=[],
            left_bboxes=[(1.0, 2.0, 3.0, 4.0)],
            right_bboxes=[(5.0, 6.0, 7.0, 8.0)],
            meta={"score": 0.42, "diff_pixels": 128},
        )
        html = build_diff_details_html(op)
        self.assertIn("Visual Difference", html)
        self.assertIn("0.420", html)
        self.assertIn("128", html)

    def test_build_field_diff_details_html_for_replace(self):
        fd = FieldDiff(
            field_name="approval_no",
            left_value="A",
            right_value="B",
            diff_type="replace",
            left_pos=(0, 5),
            right_pos=(0, 5),
        )
        html = build_field_diff_details_html(fd)
        self.assertIn("Field Value Changed", html)
        self.assertIn("approval_no", html)

    def test_populate_diff_lists_prefers_field_focus(self):
        content_list = QListWidget()
        format_list = QListWidget()
        tabs = QTabWidget()
        tabs.addTab(QListWidget(), "content (0)")
        tabs.addTab(QListWidget(), "format (0)")
        fd = FieldDiff(
            field_name="company_name",
            left_value="left co",
            right_value="right co",
            diff_type="replace",
            left_pos=(0, 3),
            right_pos=(0, 3),
        )
        focus = populate_diff_lists(
            content_list,
            format_list,
            tabs,
            [],
            compare_vm=None,
            fallback_quality_warnings=[],
            fallback_quality_scores=None,
            field_note="",
            field_diffs=[fd],
            pure_content_mode=False,
        )
        self.assertEqual(("field_diff", fd), focus)
        self.assertGreater(content_list.count(), 0)

    def test_populate_diff_lists_shows_decision_basis_metadata(self):
        content_list = QListWidget()
        format_list = QListWidget()
        tabs = QTabWidget()
        tabs.addTab(QListWidget(), "content (0)")
        tabs.addTab(QListWidget(), "format (0)")
        op = DiffOp(
            type=DiffOpType.VISUAL_DIFF,
            left_indices=[],
            right_indices=[],
            left_bboxes=[(1.0, 2.0, 3.0, 4.0)],
            right_bboxes=[(5.0, 6.0, 7.0, 8.0)],
            meta={"score": 0.12, "diff_pixels": 32},
        )
        vm = CompareViewModel(
            summary="review",
            warn=True,
            ocr_state="failure",
            ocr_state_reason="attempted_empty",
            warnings=["ocr unreliable"],
            decision_basis="raster",
            gate_reason="ocr failed",
            fallback_reason="visual_diff_after_unreliable_text_or_ocr",
            quality_scores={"left": 0, "right": 0},
        )
        populate_diff_lists(
            content_list,
            format_list,
            tabs,
            [op],
            compare_vm=vm,
            fallback_quality_warnings=[],
            fallback_quality_scores=None,
            field_note="",
            field_diffs=None,
            pure_content_mode=False,
        )
        items = [content_list.item(i).text() for i in range(content_list.count())]
        self.assertTrue(any("basis=raster" in text for text in items))
        self.assertTrue(any("visual diff" in text.lower() for text in items))


class TestInlineCharDiff(unittest.TestCase):
    def test_single_char_replacement(self):
        left_html, right_html = _inline_char_diff_html("100mg", "200mg")
        # "1" deleted on left, "2" inserted on right, "00mg" unchanged
        self.assertIn("background:#fdd", left_html)
        self.assertIn("background:#dfd", right_html)
        self.assertIn("00mg", left_html)
        self.assertIn("00mg", right_html)

    def test_multiple_changes(self):
        left_html, right_html = _inline_char_diff_html("abcde", "axcye")
        # "b" and "d" deleted on left; "x" and "y" inserted on right
        self.assertIn("background:#fdd", left_html)
        self.assertIn("background:#dfd", right_html)

    def test_insertion(self):
        left_html, right_html = _inline_char_diff_html("ac", "abc")
        # "b" inserted on right side only
        self.assertNotIn("background:#fdd", left_html)  # left has no deletions
        self.assertIn("background:#dfd", right_html)

    def test_deletion(self):
        left_html, right_html = _inline_char_diff_html("abc", "ac")
        # "b" deleted on left side only
        self.assertIn("background:#fdd", left_html)
        self.assertNotIn("background:#dfd", right_html)  # right has no insertions

    def test_empty_strings(self):
        left_html, right_html = _inline_char_diff_html("", "")
        self.assertEqual(left_html, "")
        self.assertEqual(right_html, "")

    def test_one_empty(self):
        left_html, right_html = _inline_char_diff_html("", "abc")
        self.assertEqual(left_html, "<code></code>")
        self.assertIn("background:#dfd", right_html)

    def test_identical_strings(self):
        left_html, right_html = _inline_char_diff_html("hello", "hello")
        self.assertNotIn("background:", left_html)
        self.assertNotIn("background:", right_html)
        self.assertIn("hello", left_html)
        self.assertIn("hello", right_html)

    def test_long_text_truncation(self):
        left = "a" * 100 + "X" + "b" * 200
        right = "a" * 100 + "Y" + "b" * 200
        left_html, right_html = _inline_char_diff_html(left, right)
        # Should be truncated with ellipsis markers
        self.assertIn("…", left_html)
        self.assertIn("…", right_html)
        # Changed chars should still be highlighted
        self.assertIn("background:#fdd", left_html)
        self.assertIn("background:#dfd", right_html)

    def test_unicode_cjk(self):
        left_html, right_html = _inline_char_diff_html("你好世界", "你好地球")
        # "世界" vs "地球" — 4 chars all differ
        self.assertIn("background:#fdd", left_html)
        self.assertIn("background:#dfd", right_html)

    def test_html_escaping(self):
        left_html, right_html = _inline_char_diff_html("<b>", "<i>")
        self.assertIn("&lt;", left_html)
        self.assertIn("&gt;", left_html)
        self.assertIn("&lt;", right_html)
        self.assertIn("&gt;", right_html)
        # No raw HTML tags from user text
        self.assertNotIn("<b>", left_html)
        self.assertNotIn("<i>", right_html)

    def test_replace_branch_uses_inline_diff(self):
        op = DiffOp(
            type=DiffOpType.REPLACE,
            left_indices=[0],
            right_indices=[0],
            left_bboxes=[],
            right_bboxes=[],
            meta={"left_text": "abc", "right_text": "axc"},
        )
        html = build_diff_details_html(op)
        self.assertIn("Content Changed", html)
        self.assertIn("background:#fdd", html)
        self.assertIn("background:#dfd", html)


if __name__ == "__main__":
    unittest.main()
