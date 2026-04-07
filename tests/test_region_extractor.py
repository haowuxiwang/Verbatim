from __future__ import annotations

import unittest

from core.models import CharData, PageData, StyleFlags
from core.region_extractor import (
    _bbox_strictly_within,
    _bboxes_intersect,
    _center_in,
    _normalize_bbox,
    _region_text,
    extract_region,
)


def _mk_char(ch: str, idx: int, x0: float, y0: float, x1: float, y1: float) -> CharData:
    return CharData(
        char=ch,
        index=idx,
        bbox=(x0, y0, x1, y1),
        font_name="T",
        font_family="T",
        size=10.0,
        color_rgb=(0, 0, 0),
        style=StyleFlags(),
    )


class TestRegionExtractor(unittest.TestCase):
    def setUp(self):
        chars = [
            _mk_char("A", 0, 10, 10, 20, 20),
            _mk_char("B", 1, 30, 10, 40, 20),
            _mk_char("C", 2, 10, 40, 20, 50),
        ]
        self.page = PageData(
            file_path="mem://page.pdf",
            page_number=0,
            width=200.0,
            height=200.0,
            text_chars=chars,
        )

    def test_normalize_bbox_reorders_points(self):
        self.assertEqual((1.0, 2.0, 9.0, 10.0), _normalize_bbox((9.0, 10.0, 1.0, 2.0)))

    def test_center_intersection_and_strict_within(self):
        a = (10.0, 10.0, 20.0, 20.0)
        b = (5.0, 5.0, 15.0, 15.0)
        c = (21.0, 21.0, 30.0, 30.0)
        self.assertTrue(_center_in(a, (0.0, 0.0, 30.0, 30.0)))
        self.assertTrue(_bboxes_intersect(a, b))
        self.assertFalse(_bboxes_intersect(a, c))
        self.assertTrue(_bbox_strictly_within(a, (8.0, 8.0, 22.0, 22.0)))
        self.assertFalse(_bbox_strictly_within(a, (15.0, 15.0, 25.0, 25.0)))

    def test_extract_region_intersection_mode(self):
        reg = extract_region(self.page, [(5.0, 5.0, 25.0, 25.0)], use_intersection=True)
        self.assertEqual("A", _region_text(reg.chars))

    def test_extract_region_center_mode(self):
        reg = extract_region(self.page, [(0.0, 0.0, 35.0, 30.0)], use_intersection=False)
        self.assertEqual("AB", _region_text(reg.chars))

    def test_extract_region_strict_bounds_filters_partial_overlap(self):
        reg = extract_region(
            self.page,
            [(14.0, 14.0, 16.0, 16.0)],
            use_intersection=True,
            strict_bounds=True,
        )
        self.assertEqual("", _region_text(reg.chars))

    def test_extract_region_raw_mode_keeps_original_order(self):
        page = PageData(
            file_path="mem://raw.pdf",
            page_number=0,
            width=300.0,
            height=300.0,
            text_chars=[
                _mk_char("X", 0, 100, 10, 110, 20),
                _mk_char("Y", 1, 10, 10, 20, 20),
            ],
        )
        reg = extract_region(page, [(0.0, 0.0, 150.0, 40.0)], reading_order_mode="raw")
        self.assertEqual("XY", _region_text(reg.chars))

    def test_extract_region_empty_bbox_returns_empty_region(self):
        reg = extract_region(self.page, [])
        self.assertEqual([], reg.chars)
        self.assertEqual([], reg.bboxes)


if __name__ == "__main__":
    unittest.main()
