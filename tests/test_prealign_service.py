from __future__ import annotations

import unittest

from core.pdf_parser import parse_page
from core.sample_assets import resolve_sample_path
from core.services.prealign import (
    DocumentProfile,
    PageProfile,
    build_document_profile,
    retrieve_page_candidates,
    score_page_pair,
    suggest_region_candidates,
)


class TestPrealignService(unittest.TestCase):
    def test_score_page_pair_prefers_similar_text(self):
        left = PageProfile(
            page_number=0,
            char_count=100,
            quality="good",
            confidence=90,
            layout_type="single",
            is_scanned=False,
            signature=frozenset({"药品", "说明", "明书", "用法"}),
            anchors=frozenset({"第1章", "药品", "说明书"}),
            text_sample="药品说明书",
        )
        right_similar = PageProfile(
            page_number=1,
            char_count=90,
            quality="good",
            confidence=90,
            layout_type="single",
            is_scanned=False,
            signature=frozenset({"药品", "说明", "明书", "禁忌"}),
            anchors=frozenset({"第1章", "药品", "禁忌"}),
            text_sample="药品说明书禁忌",
        )
        right_diff = PageProfile(
            page_number=2,
            char_count=80,
            quality="good",
            confidence=90,
            layout_type="single",
            is_scanned=False,
            signature=frozenset({"天气", "预报", "风速"}),
            anchors=frozenset({"第3章", "天气"}),
            text_sample="天气预报",
        )
        a = score_page_pair(left, right_similar)
        b = score_page_pair(left, right_diff)
        self.assertGreater(a.score, b.score)

    def test_failure_type_for_scanned_noise(self):
        left = PageProfile(0, 0, "bad", 60, "single", True, frozenset(), frozenset(), "")
        right = PageProfile(0, 100, "good", 90, "single", False, frozenset({"ab"}), frozenset({"第1章"}), "abc")
        c = score_page_pair(left, right)
        self.assertEqual("scanned_noise", c.failure_type)

    def test_retrieve_page_candidates_returns_topk(self):
        left_doc = DocumentProfile(
            pdf_path="left.pdf",
            page_count=1,
            pages=[
                PageProfile(
                    page_number=0,
                    char_count=100,
                    quality="good",
                    confidence=95,
                    layout_type="single",
                    is_scanned=False,
                    signature=frozenset({"ab", "bc", "cd"}),
                    anchors=frozenset({"第1章", "试验"}),
                    text_sample="abcd",
                )
            ],
            scan_ratio=0.0,
            bad_ratio=0.0,
            two_column_ratio=0.0,
        )
        right_doc = DocumentProfile(
            pdf_path="right.pdf",
            page_count=3,
            pages=[
                PageProfile(0, 100, "good", 90, "single", False, frozenset({"ab", "bc"}), frozenset({"第1章"}), "ab"),
                PageProfile(1, 100, "good", 90, "single", False, frozenset({"xy"}), frozenset({"第7章"}), "xy"),
                PageProfile(
                    2,
                    100,
                    "good",
                    90,
                    "single",
                    False,
                    frozenset({"ab", "bc", "cd"}),
                    frozenset({"第1章", "试验"}),
                    "abcd",
                ),
            ],
            scan_ratio=0.0,
            bad_ratio=0.0,
            two_column_ratio=0.0,
        )
        out = retrieve_page_candidates(left_doc, right_doc, top_k=2)
        self.assertIn(0, out)
        self.assertEqual(2, len(out[0]))
        self.assertGreaterEqual(out[0][0].score, out[0][1].score)

    def test_retrieve_page_candidates_keeps_diversity_when_scores_are_low(self):
        left_doc = DocumentProfile(
            pdf_path="left.pdf",
            page_count=1,
            pages=[
                PageProfile(
                    page_number=0,
                    char_count=80,
                    quality="good",
                    confidence=90,
                    layout_type="single",
                    is_scanned=False,
                    signature=frozenset({"ab"}),
                    anchors=frozenset({"第1章"}),
                    text_sample="ab",
                )
            ],
            scan_ratio=0.0,
            bad_ratio=0.0,
            two_column_ratio=0.0,
        )
        right_doc = DocumentProfile(
            pdf_path="right.pdf",
            page_count=2,
            pages=[
                PageProfile(0, 0, "bad", 60, "single", True, frozenset(), frozenset(), ""),
                PageProfile(1, 60, "good", 85, "single", False, frozenset({"ab"}), frozenset({"第1章"}), "ab"),
            ],
            scan_ratio=0.5,
            bad_ratio=0.5,
            two_column_ratio=0.0,
        )
        out = retrieve_page_candidates(left_doc, right_doc, top_k=2, min_score=0.10)
        got = out[0]
        self.assertEqual(2, len(got))
        self.assertEqual({0, 1}, {c.right_page for c in got})

    def test_retrieve_page_candidates_uses_position_prior_for_ordering(self):
        left_doc = DocumentProfile(
            pdf_path="left.pdf",
            page_count=2,
            pages=[
                PageProfile(0, 30, "good", 90, "single", False, frozenset({"aa"}), frozenset({"x"}), "aa"),
                PageProfile(1, 30, "good", 90, "single", False, frozenset({"bb"}), frozenset({"y"}), "bb"),
            ],
            scan_ratio=0.0,
            bad_ratio=0.0,
            two_column_ratio=0.0,
        )
        right_doc = DocumentProfile(
            pdf_path="right.pdf",
            page_count=2,
            pages=[
                PageProfile(0, 30, "good", 90, "single", False, frozenset({"aa"}), frozenset({"x"}), "aa"),
                PageProfile(1, 30, "good", 90, "single", False, frozenset({"bb"}), frozenset({"y"}), "bb"),
            ],
            scan_ratio=0.0,
            bad_ratio=0.0,
            two_column_ratio=0.0,
        )
        out = retrieve_page_candidates(left_doc, right_doc, top_k=1, min_score=0.0)
        self.assertEqual(0, out[0][0].right_page)
        self.assertEqual(1, out[1][0].right_page)

    def test_build_document_profile_smoke_real_files(self):
        left = resolve_sample_path("original.pdf")
        right = resolve_sample_path("digest.pdf")
        if not left.exists() or not right.exists():
            self.skipTest("original.pdf/digest.pdf not found in workspace")
        lp = build_document_profile(left)
        rp = build_document_profile(right)
        self.assertGreater(lp.page_count, 0)
        self.assertGreater(rp.page_count, 0)
        self.assertEqual(lp.page_count, len(lp.pages))
        self.assertEqual(rp.page_count, len(rp.pages))
        self.assertGreaterEqual(rp.scan_ratio, 0.0)

    def test_suggest_region_candidates_returns_non_empty(self):
        left = resolve_sample_path("original.pdf")
        right = resolve_sample_path("digest.pdf")
        if not left.exists() or not right.exists():
            self.skipTest("original.pdf/digest.pdf not found in workspace")
        lp = parse_page(left, 0)
        # prefer text-layer page on right to exercise anchor path when possible
        rp = parse_page(right, 1 if build_document_profile(right).page_count > 1 else 0)
        cands = suggest_region_candidates(lp, rp, top_k=2)
        self.assertGreaterEqual(len(cands), 1)
        self.assertGreater(cands[0].score, 0.0)


if __name__ == "__main__":
    unittest.main()
