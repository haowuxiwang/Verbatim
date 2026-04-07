from __future__ import annotations

import unittest

from core.services.text_quality import (
    check_text_quality,
    garble_signal_score,
    is_weak_confusable_pair,
    normalized_similarity,
    should_try_ocr_side,
)


class TestTextQualityService(unittest.TestCase):
    def test_check_text_quality_for_short_text(self):
        q = check_text_quality("abc")
        self.assertEqual(q["quality"], "bad")
        self.assertTrue(q["issues"])

    def test_normalized_similarity_ignores_spaces_and_punctuation(self):
        s = normalized_similarity("A, B. C", "A B C")
        self.assertGreaterEqual(s, 0.99)

    def test_garble_signal_score_detects_replacement_char(self):
        score, reasons = garble_signal_score("正常文本\ufffd")
        self.assertGreaterEqual(score, 3)
        self.assertTrue(reasons)

    def test_should_try_ocr_side_for_bad_quality(self):
        q = {"quality": "bad"}
        enable, reason = should_try_ocr_side("text", q)
        self.assertTrue(enable)
        self.assertIn("质量等级", reason)

    def test_weak_confusable_pair(self):
        self.assertTrue(is_weak_confusable_pair("0", "o"))
        self.assertFalse(is_weak_confusable_pair("abc", "xyz"))

    def test_low_punctuation_short_snippet_not_bad(self):
        text = "艾曲泊帕乙醇胺片说明书请仔细阅读说明书并在医师指导下使用"
        q = check_text_quality(text)
        self.assertNotEqual(q["quality"], "bad")

    def test_low_punctuation_long_text_can_be_bad(self):
        text = "这是一段没有标点的长文本" * 20
        q = check_text_quality(text)
        self.assertIn(q["quality"], {"warning", "bad"})


if __name__ == "__main__":
    unittest.main()
