import unittest

from core.layout_analyzer import LayoutInfo, LayoutType, detect_layout, sort_chars_by_reading_order
from core.models import CharData, StyleFlags


def _ch(ch: str, idx: int, x0: float, y0: float, x1: float, y1: float) -> CharData:
    return CharData(
        char=ch,
        index=idx,
        bbox=(x0, y0, x1, y1),
        font_name="F",
        font_family="F",
        size=10.0,
        color_rgb=(0, 0, 0),
        style=StyleFlags(),
    )


class TestLayoutAnalyzer(unittest.TestCase):
    def test_detect_single_column(self):
        chars = []
        idx = 0
        for row in range(30):
            y = 20.0 + row * 14.0
            for col in range(12):
                x = 90.0 + col * 14.0
                chars.append(_ch("a", idx, x, y, x + 8.0, y + 10.0))
                idx += 1

        info = detect_layout(chars, page_width=600.0)
        self.assertEqual(info.layout_type, LayoutType.SINGLE_COLUMN)

    def test_detect_two_column(self):
        chars = []
        idx = 0
        for row in range(40):
            y = 20.0 + row * 12.0
            for col in range(8):
                x = 40.0 + col * 12.0
                chars.append(_ch("l", idx, x, y, x + 7.0, y + 9.0))
                idx += 1
            for col in range(8):
                x = 340.0 + col * 12.0
                chars.append(_ch("r", idx, x, y, x + 7.0, y + 9.0))
                idx += 1

        info = detect_layout(chars, page_width=600.0)
        self.assertEqual(info.layout_type, LayoutType.TWO_COLUMN)
        self.assertEqual(info.column_count, 2)

    def test_two_column_reading_order(self):
        chars = [
            _ch("A", 0, 50.0, 20.0, 58.0, 30.0),
            _ch("B", 1, 50.0, 40.0, 58.0, 50.0),
            _ch("C", 2, 350.0, 20.0, 358.0, 30.0),
            _ch("D", 3, 350.0, 40.0, 358.0, 50.0),
        ]
        layout = LayoutInfo(LayoutType.TWO_COLUMN, 2, [250.0], 0.9)
        ordered = sort_chars_by_reading_order(chars, page_width=600.0, layout=layout)
        text = "".join(c.char for c in ordered)
        self.assertEqual(text, "ABCD")

    def test_detect_two_column_on_medium_sized_selection(self):
        chars = []
        idx = 0
        for row in range(6):
            y = 30.0 + row * 14.0
            for col in range(4):
                x = 60.0 + col * 12.0
                chars.append(_ch("l", idx, x, y, x + 7.0, y + 9.0))
                idx += 1
            for col in range(4):
                x = 330.0 + col * 12.0
                chars.append(_ch("r", idx, x, y, x + 7.0, y + 9.0))
                idx += 1

        # 48 chars total: should still be recognized as two-column layout.
        info = detect_layout(chars, page_width=600.0)
        self.assertEqual(info.layout_type, LayoutType.TWO_COLUMN)


if __name__ == "__main__":
    unittest.main()
