from __future__ import annotations

import unittest

from core.services.compare_orchestrator import (
    build_compare_result_summary,
    collect_quality_warnings,
    decide_ocr,
)
from core.services.field_orchestrator import run_field_mapping
from core.services.ocr_orchestrator import run_ocr_fallback
from core.services.text_quality import check_text_quality, should_try_ocr_side


class TestPipelineIntegration(unittest.TestCase):
    def test_compare_pipeline_without_gui(self):
        left_text = "产品名称: 阿司匹林\n规格: 100mg"
        right_text = "产品名称: 阿司匹林\n规格: 200mg"

        left_q = check_text_quality(left_text)
        right_q = check_text_quality(right_text)

        warnings, scores = collect_quality_warnings(
            left_doc_note="",
            right_doc_note="",
            left_quality=left_q,
            right_quality=right_q,
        )
        self.assertIsInstance(warnings, list)
        self.assertIn("left", scores)

        ocr_decision = decide_ocr(
            left_text=left_text,
            right_text=right_text,
            left_quality=left_q,
            right_quality=right_q,
            left_force_ocr=False,
            right_force_ocr=False,
            dual_ocr_linkage=False,
            should_try_ocr_side=should_try_ocr_side,
        )
        self.assertFalse(ocr_decision.recommended)

        ocr_result = run_ocr_fallback(
            use_ocr=False,
            has_ocr_config=False,
            left_try_ocr=ocr_decision.left_try_ocr,
            right_try_ocr=ocr_decision.right_try_ocr,
            left_text=left_text,
            right_text=right_text,
            fetch_ocr_text=lambda *_: "",
        )
        self.assertEqual(ocr_result.left_text, left_text)
        self.assertEqual(ocr_result.right_text, right_text)

        field_result = run_field_mapping(
            left_text=ocr_result.left_text,
            right_text=ocr_result.right_text,
            extract_key_values=lambda t: [{"text": t}],
            should_enable_field_mapping=lambda *_: (True, ""),
            compare_by_fields=lambda *_: [{"diff": "spec_changed"}],
        )
        self.assertTrue(field_result.enabled)
        self.assertEqual(len(field_result.field_diffs), 1)

        summary = build_compare_result_summary(
            left_ocr_applied=ocr_result.left_ocr_applied,
            right_ocr_applied=ocr_result.right_ocr_applied,
            dual_ocr_mode=False,
            pure_content=True,
            show_format_diffs=False,
        )
        self.assertIn("纯内容: 开", summary)


if __name__ == "__main__":
    unittest.main()
