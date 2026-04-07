from __future__ import annotations

import logging
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from core.diff_engine import (
    _greedy_match_pairs,
    _lcs_match_pairs,
    _merge_key_value_lines,
    _normalize_numbers,
    _op_to_dict,
    chars_are_similar,
    diff_text,
    normalize_text,
)
from core.models import DiffOp, DiffOpType


class TestDiffEngine(unittest.TestCase):
    def test_normalize_text_pure_content(self):
        self.assertEqual("abc", normalize_text("a  b\nc", remove_all_whitespace=True))
        self.assertEqual("a b c", normalize_text("a\r\nb\nc", aggressive=False))

    def test_merge_key_value_lines_and_numbers(self):
        merged = _merge_key_value_lines("电话:\n0576-88827887\n地址:\n杭州")
        self.assertIn("电话: 0576-88827887", merged)
        self.assertEqual("1000 mg", _normalize_numbers("1,000 mg"))

    def test_chars_are_similar(self):
        self.assertTrue(chars_are_similar("l", "1"))
        self.assertTrue(chars_are_similar("O", "0"))
        self.assertFalse(chars_are_similar("a", "b"))

    def test_lcs_and_greedy_pairs(self):
        pairs = _lcs_match_pairs("abc", "axbc")
        self.assertEqual([(0, 0), (1, 2), (2, 3)], pairs)
        large_a = "a" * 1002 + "b"
        large_b = "a" * 1001 + "cb"
        gpairs = _lcs_match_pairs(large_a, large_b)
        self.assertGreater(len(gpairs), 900)
        gpairs2 = _greedy_match_pairs("abc", "aXbc", use_similarity=False)
        self.assertGreaterEqual(len(gpairs2), 1)

    def test_diff_text_add_del_replace(self):
        ops = diff_text("abc", "abXc")
        self.assertGreaterEqual(len(ops), 1)
        self.assertIn(ops[0].type, {DiffOpType.ADD, DiffOpType.REPLACE})

        same = diff_text("a b", "ab", pure_content_mode=True)
        self.assertEqual([], same)

    def test_diff_text_with_v1_options(self):
        a = "价格: 1,000 元"
        b = "价格 1000 元"
        ops = diff_text(a, b, remove_punctuation=True, normalize_numbers=True, merge_key_value_lines=True)
        self.assertEqual([], ops)

    def test_op_to_dict(self):
        op = DiffOp(
            type=DiffOpType.REPLACE,
            left_indices=[0],
            right_indices=[0],
            left_bboxes=[],
            right_bboxes=[],
            meta={"left_text": "a", "right_text": "b"},
        )
        d = _op_to_dict(op)
        self.assertEqual("replace", d["type"])
        self.assertIn("meta", d)

    def test_logger_initialization_smoke(self):
        # Use a temporary cwd to avoid touching repo logs in this test.
        logger = logging.getLogger("verbatim")
        for h in list(logger.handlers):
            logger.removeHandler(h)
            try:
                h.close()
            except Exception:
                pass
        old = Path.cwd()
        td = tempfile.mkdtemp()
        try:
            os.chdir(td)
            out = diff_text("abc", "abd")
            self.assertGreaterEqual(len(out), 1)
            self.assertTrue((Path(td) / "logs" / "verbatim.log").exists())
        finally:
            os.chdir(old)
            logger2 = logging.getLogger("verbatim")
            for h in list(logger2.handlers):
                logger2.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
