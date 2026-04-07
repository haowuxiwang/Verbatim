from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.auto_match import (
    _calculate_text_similarity,
    _calculate_y_overlap,
    _extract_paragraphs,
    analyze_document_structure,
    create_regions_from_text_selection,
    load_suggested_mappings,
    save_suggested_mappings,
    suggest_mappings,
)
from core.models import CharData, PageData, RegionData, StyleFlags


class TestAutoMatch(unittest.TestCase):
    def _make_page(self, text: str, *, page_number: int = 0) -> PageData:
        chars = []
        x = 0.0
        for i, ch in enumerate(text):
            chars.append(
                CharData(
                    char=ch,
                    index=i,
                    bbox=(x, 0.0, x + 1.0, 1.0),
                    font_name="F",
                    font_family="F",
                    size=10.0,
                    color_rgb=(0, 0, 0),
                    style=StyleFlags(),
                )
            )
            x += 1.0
        return PageData(
            file_path="x.pdf",
            page_number=page_number,
            width=max(1.0, x),
            height=100.0,
            text_chars=chars,
        )

    def test_suggest_mappings_does_not_crash_with_page_data(self):
        left = self._make_page("Hello World")
        right = self._make_page("Hello World")
        result = suggest_mappings(left, right, max_candidates_per_region=2)
        self.assertIsInstance(result, list)

    def test_suggest_mappings_returns_triples(self):
        left = self._make_page("Drug instruction")
        right = self._make_page("Drug instruction")
        result = suggest_mappings(left, right, max_candidates_per_region=3)
        for item in result:
            self.assertEqual(len(item), 3)

    def test_text_similarity_and_overlap_helpers(self):
        self.assertAlmostEqual(1.0, _calculate_text_similarity("abc", "abc"), places=2)
        self.assertAlmostEqual(0.0, _calculate_text_similarity("", "x"), places=2)
        self.assertGreater(_calculate_y_overlap((0, 0, 10, 10), (0, 5, 10, 15)), 0.0)
        self.assertEqual(0.0, _calculate_y_overlap((0, 0, 10, 10), (0, 11, 10, 20)))

    def test_extract_paragraphs_and_structure(self):
        chars = self._make_page("abc\ndefghij").text_chars
        paras = _extract_paragraphs(chars)
        self.assertGreaterEqual(len(paras), 1)
        page = self._make_page("helloworld")
        regions = analyze_document_structure(page)
        self.assertGreaterEqual(len(regions), 1)
        self.assertIsInstance(regions[0], RegionData)

    def test_create_regions_from_text_selection(self):
        page = self._make_page("drugname dosage")
        regions = create_regions_from_text_selection(page, "drug", fuzz_threshold=0.5)
        self.assertGreaterEqual(len(regions), 1)

    def test_save_and_load_suggested_mappings(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "mappings.json"
            data = [(0, 1, 0.9), (2, 3, 0.8)]
            save_suggested_mappings(data, p)
            loaded = load_suggested_mappings(p)
            self.assertEqual(data, loaded)
            p.write_text('{"type":"other"}', encoding="utf-8")
            self.assertIsNone(load_suggested_mappings(p))


if __name__ == "__main__":
    unittest.main()
