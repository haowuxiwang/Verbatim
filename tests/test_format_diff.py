import unittest

from core.format_diff import format_diff_regions
from core.models import CharData, RegionData, StyleFlags


def _mk_char(
    ch: str,
    index: int,
    *,
    size: float = 10.0,
    rgb=(0, 0, 0),
    font_family: str = "F",
    bold: bool = False,
    italic: bool = False,
    bbox=(0.0, 0.0, 1.0, 1.0),
) -> CharData:
    return CharData(
        char=ch,
        index=index,
        bbox=bbox,
        font_name=font_family,
        font_family=font_family,
        size=size,
        color_rgb=rgb,
        style=StyleFlags(bold=bold, italic=italic),
    )


def _region(chars) -> RegionData:
    return RegionData(page_number=0, bboxes=[], chars=chars)


class TestFormatDiff(unittest.TestCase):
    def test_size_threshold(self):
        left = _region([_mk_char("a", 0, size=10.0), _mk_char("b", 1, size=10.0)])
        right_no = _region([_mk_char("a", 0, size=10.0), _mk_char("b", 1, size=10.4)])
        self.assertEqual(format_diff_regions(left, right_no), [])

        right_yes = _region([_mk_char("a", 0, size=10.0), _mk_char("b", 1, size=10.5)])
        ops = format_diff_regions(left, right_yes)
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0].type.value, "format_change")
        self.assertIn("size", ops[0].meta["reasons"])

    def test_color_threshold(self):
        left = _region([_mk_char("a", 0, rgb=(0, 0, 0))])
        right_no = _region([_mk_char("a", 0, rgb=(0, 0, 14))])
        self.assertEqual(format_diff_regions(left, right_no), [])

        right_yes = _region([_mk_char("a", 0, rgb=(0, 0, 15))])
        ops = format_diff_regions(left, right_yes)
        self.assertEqual(len(ops), 1)
        self.assertIn("color_rgb", ops[0].meta["reasons"])

    def test_font_and_style_changes(self):
        left = _region([_mk_char("a", 0, font_family="Arial", bold=False, italic=False)])
        right = _region([_mk_char("a", 0, font_family="Times", bold=True, italic=True)])
        ops = format_diff_regions(left, right)
        self.assertEqual(len(ops), 1)
        self.assertIn("font_family", ops[0].meta["reasons"])
        self.assertIn("bold", ops[0].meta["reasons"])
        self.assertIn("italic", ops[0].meta["reasons"])

    def test_grouping_contiguous_changes(self):
        left = _region(
            [
                _mk_char("a", 10, size=10.0),
                _mk_char("b", 11, size=10.0),
                _mk_char("c", 12, size=10.0),
            ]
        )
        right = _region(
            [
                _mk_char("a", 20, size=10.0),
                _mk_char("b", 21, size=11.0),
                _mk_char("c", 22, size=11.0),
            ]
        )
        ops = format_diff_regions(left, right)
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0].left_indices, [11, 12])
        self.assertEqual(ops[0].right_indices, [21, 22])

    def test_alignment_with_insertion(self):
        # right inserts X between a and b; format change should still be detected on b.
        left = _region([_mk_char("a", 0), _mk_char("b", 1, bold=False), _mk_char("c", 2)])
        right = _region(
            [
                _mk_char("a", 0),
                _mk_char("X", 1),
                _mk_char("b", 2, bold=True),
                _mk_char("c", 3),
            ]
        )
        ops = format_diff_regions(left, right)
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0].left_indices, [1])
        self.assertEqual(ops[0].right_indices, [2])
        self.assertIn("bold", ops[0].meta["reasons"])


if __name__ == "__main__":
    unittest.main()
