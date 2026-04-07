"""Performance tests for diff operations."""

from __future__ import annotations

import time
import unittest

from core.diff_regions import diff_regions
from core.models import CharData, RegionData, StyleFlags


class TestPerformance(unittest.TestCase):
    def test_small_region_diff(self):
        """Test diff on small regions (expected < 100ms)."""
        # Create a small text region
        chars = [
            CharData("Hello", 0, (0, 0, 50, 20), "Arial", "Arial", 12.0, (0, 0, 0), StyleFlags()),
            CharData(" ", 1, (50, 0, 60, 20), "Arial", "Arial", 12.0, (0, 0, 0), StyleFlags()),
            CharData("World", 2, (60, 0, 110, 20), "Arial", "Arial", 12.0, (0, 0, 0), StyleFlags()),
        ]

        region = RegionData(page_number=0, bboxes=[(0, 0, 110, 20)], chars=chars)

        start_time = time.time()
        ops, norm_log = diff_regions(region, region)  # Updated: returns tuple
        end_time = time.time()

        duration = (end_time - start_time) * 1000  # ms
        print(f"Small region diff took {duration:.2f}ms")

        # Should be very fast for identical small regions
        self.assertLess(duration, 50)
        self.assertEqual(len(ops), 0)  # No differences

    def test_medium_region_diff(self):
        """Test diff on medium regions (expected < 300ms)."""
        # Create a medium text region (about 1000 characters)
        chars = []
        text = "This is a test sentence. " * 20  # ~600 characters

        x_pos = 0
        for i, word in enumerate(text.split()):
            for char in word:
                chars.append(
                    CharData(char, i, (x_pos, 0, x_pos + 10, 20), "Arial", "Arial", 12.0, (0, 0, 0), StyleFlags())
                )
                x_pos += 10

        region = RegionData(page_number=0, bboxes=[(0, 0, x_pos, 20)], chars=chars)

        start_time = time.time()
        ops, norm_log = diff_regions(region, region)  # Updated: returns tuple
        end_time = time.time()

        duration = (end_time - start_time) * 1000  # ms
        print(f"Medium region diff took {duration:.2f}ms")

        # Should be under 300ms
        self.assertLess(duration, 300)
        self.assertEqual(len(ops), 0)  # No differences

    def test_large_region_diff(self):
        """Test diff on large regions (performance warning)."""
        # Create a large text region (about 10000 characters)
        chars = []
        text = "This is a test sentence. " * 200  # ~6000 characters

        x_pos = 0
        for i, word in enumerate(text.split()):
            for char in word:
                chars.append(
                    CharData(char, i, (x_pos, 0, x_pos + 10, 20), "Arial", "Arial", 12.0, (0, 0, 0), StyleFlags())
                )
                x_pos += 10

        region = RegionData(page_number=0, bboxes=[(0, 0, x_pos, 20)], chars=chars)

        start_time = time.time()
        ops, norm_log = diff_regions(region, region)  # Updated: returns tuple
        end_time = time.time()

        duration = (end_time - start_time) * 1000  # ms
        print(f"Large region diff took {duration:.2f}ms")

        # For large regions, it might take longer but should still be reasonable
        self.assertLess(duration, 1000)  # 1 second max
        self.assertEqual(len(ops), 0)  # No differences

    def test_different_text_diff(self):
        """Test diff with completely different text."""
        # Create two completely different regions
        left_chars = []
        right_chars = []

        left_text = "The quick brown fox jumps over the lazy dog."
        right_text = "A slow white cat walks past the sleeping elephant."

        x_pos = 0
        for char in left_text:
            left_chars.append(
                CharData(
                    char, len(left_chars), (x_pos, 0, x_pos + 10, 20), "Arial", "Arial", 12.0, (0, 0, 0), StyleFlags()
                )
            )
            x_pos += 10

        x_pos = 0
        for char in right_text:
            right_chars.append(
                CharData(
                    char, len(right_chars), (x_pos, 0, x_pos + 10, 20), "Arial", "Arial", 12.0, (0, 0, 0), StyleFlags()
                )
            )
            x_pos += 10

        left_region = RegionData(0, [(0, 0, x_pos, 20)], left_chars)
        right_region = RegionData(0, [(0, 0, x_pos, 20)], right_chars)

        start_time = time.time()
        ops, norm_log = diff_regions(left_region, right_region)  # Updated: returns tuple
        end_time = time.time()

        duration = (end_time - start_time) * 1000  # ms
        print(f"Different text diff took {duration:.2f}ms")

        # Should be fast even with completely different text
        self.assertLess(duration, 500)
        self.assertGreater(len(ops), 0)  # Should have many differences


if __name__ == "__main__":
    unittest.main()
