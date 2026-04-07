from __future__ import annotations

import unittest

from core.services.compare_orchestrator import (
    build_compare_result_summary,
    collect_quality_warnings,
    decide_ocr,
)


class TestCompareOrchestrator(unittest.TestCase):
    def test_collect_quality_warnings(self):
        left_q = {"confidence": 60, "issues": ["问题A"]}
        right_q = {"confidence": 90, "issues": []}
        warnings, scores = collect_quality_warnings(
            left_doc_note="左侧文档提示",
            right_doc_note="",
            left_quality=left_q,
            right_quality=right_q,
        )
        self.assertEqual(scores["left"], 60)
        self.assertEqual(scores["right"], 90)
        self.assertGreaterEqual(len(warnings), 2)

    def test_decide_ocr_dual_linkage(self):
        def fake_should_try(_text, quality):
            return (quality.get("quality") == "bad", "质量等级=bad")

        d = decide_ocr(
            left_text="a",
            right_text="b",
            left_quality={"quality": "bad"},
            right_quality={"quality": "good"},
            left_force_ocr=False,
            right_force_ocr=False,
            dual_ocr_linkage=True,
            should_try_ocr_side=fake_should_try,
        )
        self.assertTrue(d.left_try_ocr)
        self.assertTrue(d.right_try_ocr)
        self.assertTrue(d.recommended)

    def test_build_compare_result_summary(self):
        s = build_compare_result_summary(
            left_ocr_applied=True,
            right_ocr_applied=False,
            dual_ocr_mode=False,
            pure_content=True,
            show_format_diffs=False,
        )
        self.assertIn("OCR: 仅左侧", s)
        self.assertIn("纯内容: 开", s)


if __name__ == "__main__":
    unittest.main()
