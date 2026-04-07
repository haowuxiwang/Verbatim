from __future__ import annotations

import unittest

from app.page_quality import evaluate_page_text_layer


class TestPageQuality(unittest.TestCase):
    def test_non_empty_page_does_not_force_ocr(self):
        status = evaluate_page_text_layer(side_label="左侧", page_number=0, text_char_count=12)
        self.assertFalse(status.force_ocr)
        self.assertEqual("", status.brief_log)
        self.assertEqual((), status.warning_banner)

    def test_empty_page_generates_warning_and_force_ocr(self):
        status = evaluate_page_text_layer(side_label="右侧", page_number=3, text_char_count=0)
        self.assertTrue(status.force_ocr)
        self.assertIn("Right", status.brief_log.replace("右侧", "Right"))
        self.assertEqual(4, len(status.warning_banner))
        self.assertIn("扫描版", status.warning_banner[1])


if __name__ == "__main__":
    unittest.main()
