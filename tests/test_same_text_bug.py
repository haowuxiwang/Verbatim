"""Test to reproduce the same text diff bug."""

from __future__ import annotations

from core.diff_regions import diff_regions
from core.models import CharData, RegionData, StyleFlags


def test_same_text_diff_bug():
    """Test case that reproduces the bug where same text shows as diff."""

    # Create identical text in two different regions
    chars_left = [
        CharData("准", 0, (100, 200, 120, 220), "SimSun", "SimSun", 12.0, (0, 0, 0), StyleFlags()),
        CharData("核", 1, (120, 200, 140, 220), "SimSun", "SimSun", 12.0, (0, 0, 0), StyleFlags()),
        CharData("日", 2, (140, 200, 160, 220), "SimSun", "SimSun", 12.0, (0, 0, 0), StyleFlags()),
        CharData("期", 3, (160, 200, 180, 220), "SimSun", "SimSun", 12.0, (0, 0, 0), StyleFlags()),
        CharData("：", 4, (180, 200, 190, 220), "SimSun", "SimSun", 12.0, (0, 0, 0), StyleFlags()),
    ]

    chars_right = [
        CharData("准", 10, (200, 300, 220, 320), "SimSun", "SimSun", 12.0, (0, 0, 0), StyleFlags()),
        CharData("核", 11, (220, 300, 240, 320), "SimSun", "SimSun", 12.0, (0, 0, 0), StyleFlags()),
        CharData("日", 12, (240, 300, 260, 320), "SimSun", "SimSun", 12.0, (0, 0, 0), StyleFlags()),
        CharData("期", 13, (260, 300, 280, 320), "SimSun", "SimSun", 12.0, (0, 0, 0), StyleFlags()),
        CharData("：", 14, (280, 300, 290, 320), "SimSun", "SimSun", 12.0, (0, 0, 0), StyleFlags()),
    ]

    left_region = RegionData(0, [(100, 200, 190, 220)], chars_left)
    right_region = RegionData(0, [(200, 300, 290, 320)], chars_right)

    # This should show no differences since text is identical
    # diff_regions returns tuple[list[DiffOp], NormalizationLog]
    ops, norm_log = diff_regions(left_region, right_region)

    print(f"Number of diff operations: {len(ops)}")
    for op in ops:
        print(f"  Type: {op.type}")
        print(f"  Left indices: {op.left_indices}")
        print(f"  Right indices: {op.right_indices}")
        if op.meta:
            print(f"  Meta: {op.meta}")
        print()

    # This should be 0 - no differences for identical text
    assert len(ops) == 0, f"Expected 0 operations, got {len(ops)}"
    print("✓ Test passed: Same text produces no diff")


if __name__ == "__main__":
    test_same_text_diff_bug()
