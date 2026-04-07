from __future__ import annotations

import unittest

from core.services.ocr_orchestrator import run_ocr_fallback


class TestOcrOrchestrator(unittest.TestCase):
    def test_skip_when_no_config(self):
        r = run_ocr_fallback(
            use_ocr=True,
            has_ocr_config=False,
            left_try_ocr=True,
            right_try_ocr=True,
            left_text="L",
            right_text="R",
            fetch_ocr_text=lambda *_: "",
        )
        self.assertTrue(r.skipped_no_config)
        self.assertFalse(r.ocr_used)

    def test_apply_left_only(self):
        r = run_ocr_fallback(
            use_ocr=True,
            has_ocr_config=True,
            left_try_ocr=True,
            right_try_ocr=False,
            left_text="L",
            right_text="R",
            fetch_ocr_text=lambda side, *_: "L2" if side == "left" else "",
        )
        self.assertTrue(r.left_ocr_applied)
        self.assertFalse(r.right_ocr_applied)
        self.assertEqual(r.left_text, "L2")
        self.assertIn("仅非OCR侧支持定位高亮", r.ocr_note)

    def test_attempted_but_empty(self):
        r = run_ocr_fallback(
            use_ocr=True,
            has_ocr_config=True,
            left_try_ocr=True,
            right_try_ocr=True,
            left_text="L",
            right_text="R",
            fetch_ocr_text=lambda *_: "",
        )
        self.assertTrue(r.attempted_but_empty)
        self.assertFalse(r.ocr_used)


if __name__ == "__main__":
    unittest.main()
