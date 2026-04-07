from __future__ import annotations

import unittest

from core.services.field_orchestrator import run_field_mapping


class TestFieldOrchestrator(unittest.TestCase):
    def test_enabled_mapping(self):
        result = run_field_mapping(
            left_text="a",
            right_text="b",
            extract_key_values=lambda t: [t],
            should_enable_field_mapping=lambda *_: (True, ""),
            compare_by_fields=lambda *_: ["diff1"],
        )
        self.assertTrue(result.enabled)
        self.assertEqual(result.field_diffs, ["diff1"])
        self.assertEqual(result.note, "")

    def test_disabled_mapping(self):
        result = run_field_mapping(
            left_text="a",
            right_text="b",
            extract_key_values=lambda t: [t],
            should_enable_field_mapping=lambda *_: (False, "结构化证据不足"),
            compare_by_fields=lambda *_: ["should-not-run"],
        )
        self.assertFalse(result.enabled)
        self.assertEqual(result.field_diffs, [])
        self.assertIn("字段比对已跳过", result.note)


if __name__ == "__main__":
    unittest.main()
