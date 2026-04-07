from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from core.services.raster_diff import compute_visual_diff_payload


def _png_bytes(color: int, *, size: tuple[int, int] = (10, 10)) -> bytes:
    img = Image.new("L", size, color=color)
    from io import BytesIO

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestRasterDiff(unittest.TestCase):
    def test_compute_visual_diff_payload_returns_empty_for_identical_images(self):
        rendered = SimpleNamespace(image_bytes=_png_bytes(255), clip_bbox=(0.0, 0.0, 100.0, 100.0))
        with patch("core.services.raster_diff.render_pdf_region_png_with_meta", side_effect=[rendered, rendered]):
            payload = compute_visual_diff_payload(
                "left.pdf",
                "right.pdf",
                0,
                0,
                (0.0, 0.0, 100.0, 100.0),
                (0.0, 0.0, 100.0, 100.0),
            )
        self.assertEqual([], payload)

    def test_compute_visual_diff_payload_returns_bbox_for_changed_images(self):
        left = SimpleNamespace(image_bytes=_png_bytes(255), clip_bbox=(0.0, 0.0, 100.0, 100.0))
        right_img = Image.new("L", (10, 10), color=255)
        right_img.putpixel((4, 4), 0)
        from io import BytesIO

        buf = BytesIO()
        right_img.save(buf, format="PNG")
        right = SimpleNamespace(image_bytes=buf.getvalue(), clip_bbox=(0.0, 0.0, 100.0, 100.0))
        with patch("core.services.raster_diff.render_pdf_region_png_with_meta", side_effect=[left, right]):
            payload = compute_visual_diff_payload(
                "left.pdf",
                "right.pdf",
                0,
                0,
                (0.0, 0.0, 100.0, 100.0),
                (0.0, 0.0, 100.0, 100.0),
            )
        self.assertEqual(1, len(payload))
        self.assertGreater(payload[0]["diff_pixels"], 0)
        self.assertEqual(4, len(payload[0]["left_bbox"]))
        self.assertEqual(4, len(payload[0]["right_bbox"]))


if __name__ == "__main__":
    unittest.main()
