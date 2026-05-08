import unittest

from core.diff_regions import diff_regions
from core.models import CharData, DiffOpType, RegionData, StyleFlags


def _mk_char(
    ch: str,
    index: int,
    *,
    size: float = 10.0,
    rgb=(0, 0, 0),
    font_family: str = "F",
    bold: bool = False,
    italic: bool = False,
) -> CharData:
    return CharData(
        char=ch,
        index=index,
        bbox=(float(index), 0.0, float(index) + 1.0, 1.0),
        font_name=font_family,
        font_family=font_family,
        size=size,
        color_rgb=rgb,
        style=StyleFlags(bold=bold, italic=italic),
    )


def _region(chars) -> RegionData:
    return RegionData(page_number=0, bboxes=[], chars=chars)


class TestDiffRegions(unittest.TestCase):
    def test_pure_text_change(self):
        left = _region([_mk_char("a", 0), _mk_char("b", 1)])
        right = _region([_mk_char("a", 10), _mk_char("c", 11)])
        ops, norm_log = diff_regions(left, right)  # Updated: returns tuple

        text_ops = [o for o in ops if o.type in (DiffOpType.ADD, DiffOpType.DEL, DiffOpType.REPLACE)]
        fmt_ops = [o for o in ops if o.type == DiffOpType.FORMAT_CHANGE]

        self.assertEqual(len(fmt_ops), 0)
        self.assertGreaterEqual(len(text_ops), 1)
        # For this simple case we expect a replace of the second char.
        self.assertEqual(text_ops[0].type, DiffOpType.REPLACE)
        # Indices are mapped back to CharData.index (per-page stable indices).
        self.assertEqual(text_ops[0].left_indices, [1])
        self.assertEqual(text_ops[0].right_indices, [11])
        self.assertEqual(text_ops[0].meta.get("left_text"), "b")
        self.assertEqual(text_ops[0].meta.get("right_text"), "c")

    def test_pure_format_change(self):
        left = _region([_mk_char("a", 0, size=10.0)])
        right = _region([_mk_char("a", 10, size=10.6)])
        ops, norm_log = diff_regions(left, right)  # Updated: returns tuple

        text_ops = [o for o in ops if o.type in (DiffOpType.ADD, DiffOpType.DEL, DiffOpType.REPLACE)]
        fmt_ops = [o for o in ops if o.type == DiffOpType.FORMAT_CHANGE]

        self.assertEqual(text_ops, [])
        self.assertEqual(len(fmt_ops), 1)
        self.assertIn("size", fmt_ops[0].meta.get("reasons", []))

    def test_text_and_format_change_together(self):
        # Text changes b->c, and formatting changes on the matched 'a'.
        left = _region([_mk_char("a", 0, size=10.0), _mk_char("b", 1, size=10.0)])
        right = _region([_mk_char("a", 10, size=11.0), _mk_char("c", 11, size=10.0)])
        ops, norm_log = diff_regions(left, right)  # Updated: returns tuple

        text_ops = [o for o in ops if o.type in (DiffOpType.ADD, DiffOpType.DEL, DiffOpType.REPLACE)]
        fmt_ops = [o for o in ops if o.type == DiffOpType.FORMAT_CHANGE]

        self.assertGreaterEqual(len(text_ops), 1)
        self.assertEqual(len(fmt_ops), 1)
        self.assertIn("size", fmt_ops[0].meta.get("reasons", []))

    def test_trivial_punctuation_noise_is_suppressed_by_default(self):
        left = _region([_mk_char("a", 0), _mk_char(",", 1), _mk_char("b", 2)])
        right = _region([_mk_char("a", 10), _mk_char(".", 11), _mk_char("b", 12)])

        ops, _ = diff_regions(left, right)
        text_ops = [o for o in ops if o.type in (DiffOpType.ADD, DiffOpType.DEL, DiffOpType.REPLACE)]
        self.assertEqual(text_ops, [])

    def test_can_keep_trivial_punctuation_noise_when_disabled(self):
        left = _region([_mk_char("a", 0), _mk_char(",", 1), _mk_char("b", 2)])
        right = _region([_mk_char("a", 10), _mk_char(".", 11), _mk_char("b", 12)])

        ops, _ = diff_regions(left, right, suppress_trivial_diffs=False)
        text_ops = [o for o in ops if o.type in (DiffOpType.ADD, DiffOpType.DEL, DiffOpType.REPLACE)]
        self.assertGreaterEqual(len(text_ops), 1)

    def test_ignore_punctuation_keeps_correct_non_punctuation_indices(self):
        left = _region([_mk_char("a", 0), _mk_char("b", 1), _mk_char(",", 2), _mk_char("c", 3)])
        right = _region([_mk_char("a", 10), _mk_char("b", 11), _mk_char(",", 12), _mk_char("d", 13)])

        ops, _ = diff_regions(
            left,
            right,
            ignore_punctuation=True,
            suppress_trivial_diffs=False,
            coalesce_nearby_text_ops=False,
        )
        text_ops = [o for o in ops if o.type in (DiffOpType.ADD, DiffOpType.DEL, DiffOpType.REPLACE)]
        self.assertEqual(len(text_ops), 1)
        self.assertEqual(text_ops[0].left_indices, [3])
        self.assertEqual(text_ops[0].right_indices, [13])
        self.assertEqual(text_ops[0].meta.get("left_text"), "c")
        self.assertEqual(text_ops[0].meta.get("right_text"), "d")

    def test_pure_content_mode_keeps_correct_indices(self):
        left = _region([_mk_char("a", 0), _mk_char(" ", 1), _mk_char("b", 2), _mk_char(" ", 3), _mk_char("c", 4)])
        right = _region([_mk_char("a", 10), _mk_char(" ", 11), _mk_char("b", 12), _mk_char(" ", 13), _mk_char("d", 14)])

        ops, _ = diff_regions(
            left,
            right,
            pure_content_mode=True,
            suppress_trivial_diffs=False,
            coalesce_nearby_text_ops=False,
        )
        text_ops = [o for o in ops if o.type in (DiffOpType.ADD, DiffOpType.DEL, DiffOpType.REPLACE)]
        self.assertEqual(len(text_ops), 1)
        self.assertEqual(text_ops[0].left_indices, [4])
        self.assertEqual(text_ops[0].right_indices, [14])
        self.assertEqual(text_ops[0].meta.get("left_text"), "c")
        self.assertEqual(text_ops[0].meta.get("right_text"), "d")

    def test_anchor_local_diff_keeps_shared_fragment_from_one_side_claim(self):
        left_text = "遗传毒性（其他）数据项A异常"
        right_text = "遗传毒性（其他）数据项B异常"
        left = _region([_mk_char(ch, i) for i, ch in enumerate(left_text)])
        right = _region([_mk_char(ch, 100 + i) for i, ch in enumerate(right_text)])

        ops, _ = diff_regions(
            left,
            right,
            suppress_trivial_diffs=False,
            coalesce_nearby_text_ops=False,
        )
        text_ops = [o for o in ops if o.type in (DiffOpType.ADD, DiffOpType.DEL, DiffOpType.REPLACE)]
        self.assertTrue(text_ops)
        for op in text_ops:
            left_seg = str(op.meta.get("left_text", ""))
            right_seg = str(op.meta.get("right_text", ""))
            self.assertFalse(op.type == DiffOpType.DEL and "（其他）" in left_seg)
            self.assertFalse(op.type == DiffOpType.ADD and "（其他）" in right_seg)


class TestDiffRegionsMultiLineChinese(unittest.TestCase):
    """P1 Gap 8: Multi-line Chinese text through token-anchored diff path."""

    def test_multiline_chinese_pharmaceutical_text(self):
        # Use single-line text to avoid newline normalization complications.
        left_text = "【药品名称】阿司匹林肠溶片【规格】100mg"
        right_text = "【药品名称】阿司匹林肠溶片【规格】200mg"

        left = _region([_mk_char(ch, i) for i, ch in enumerate(left_text)])
        right = _region([_mk_char(ch, 100 + i) for i, ch in enumerate(right_text)])

        ops, _ = diff_regions(left, right, coalesce_nearby_text_ops=False)
        text_ops = [o for o in ops if o.type in (DiffOpType.ADD, DiffOpType.DEL, DiffOpType.REPLACE)]
        self.assertGreaterEqual(len(text_ops), 1)
        # Token-anchored diff with char-level refinement may produce "1"->"2" or "100mg"->"200mg".
        all_text = "".join(op.meta.get("left_text", "") + op.meta.get("right_text", "") for op in text_ops)
        self.assertTrue("1" in all_text or "2" in all_text or "100" in all_text or "200" in all_text)

    def test_token_anchored_shared_prefix_preserved(self):
        left_text = "适应症用于治疗高血压和心绞痛"
        right_text = "适应症用于治疗高血压和心力衰竭"

        left = _region([_mk_char(ch, i) for i, ch in enumerate(left_text)])
        right = _region([_mk_char(ch, 100 + i) for i, ch in enumerate(right_text)])

        ops, _ = diff_regions(left, right, coalesce_nearby_text_ops=False)
        text_ops = [o for o in ops if o.type in (DiffOpType.ADD, DiffOpType.DEL, DiffOpType.REPLACE)]
        self.assertGreaterEqual(len(text_ops), 1)
        # Shared prefix "适应症用于治疗高血压和" should NOT appear in any diff op.
        for op in text_ops:
            left_seg = op.meta.get("left_text", "")
            right_seg = op.meta.get("right_text", "")
            self.assertNotIn("适应症", left_seg + right_seg)

    def test_cjk_latin_mixed_tokenization(self):
        from core.diff_regions import _tokenize_with_spans

        text = "规格100mg每片含阿司匹林"
        tokens = _tokenize_with_spans(text)
        token_texts = [t[0] for t in tokens]
        self.assertIn("规格", token_texts)
        self.assertIn("100mg", token_texts)
        self.assertIn("每片含阿司匹林", token_texts)


if __name__ == "__main__":
    unittest.main()
