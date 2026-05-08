from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz

from core.models import BBox, PageData
from core.ocr_client import RenderedRegion, render_pdf_region_png_with_meta
from core.pdf_parser import parse_page
from core.services.prealign import (
    DocumentProfile,
    PageCandidate,
    RegionCandidate,
    build_document_profile,
    retrieve_page_candidates,
    suggest_region_candidates,
)
from core.services.raster_diff import compute_visual_diff_payload
from core.services.text_quality import check_text_quality


@dataclass(frozen=True)
class PageLoadResult:
    png_bytes: bytes
    page: PageData


@dataclass(frozen=True)
class PrealignComputationResult:
    page_candidates: list[PageCandidate]
    items_payload: list[tuple[int, int, BBox, BBox, float, str]]


def render_page_png(pdf_path: str | Path, page_number: int = 0, zoom: float = 2.0) -> bytes:
    with fitz.open(Path(pdf_path)) as doc:
        page = doc.load_page(int(page_number))
        mat = fitz.Matrix(float(zoom), float(zoom))
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB, alpha=False)
        return pix.tobytes("png")


def load_page_bundle(pdf_path: str | Path, page_number: int = 0, zoom: float = 2.0) -> PageLoadResult:
    return PageLoadResult(
        png_bytes=render_page_png(pdf_path, page_number=page_number, zoom=zoom),
        page=parse_page(pdf_path, page_number),
    )


def parse_page_task(pdf_path: str | Path, page_number: int) -> PageData:
    return parse_page(pdf_path, int(page_number))


def assess_pdf_side_quality(pdf_path: str | Path, page_count: int, side: str) -> tuple[bool, str]:
    sample_pages = _sample_page_indices(page_count, max_samples=5)
    if not sample_pages:
        return False, f"{side}: 无可评估页面"

    scanned_pages = 0
    bad_pages = 0
    warning_pages = 0

    for p in sample_pages:
        try:
            page = parse_page(pdf_path, p)
            text = "".join(ch.char for ch in page.text_chars)
            if not text.strip():
                scanned_pages += 1
                continue
            q = check_text_quality(text)
            if q.get("quality") == "bad":
                bad_pages += 1
            elif q.get("quality") == "warning":
                warning_pages += 1
        except Exception:
            bad_pages += 1

    total = len(sample_pages)
    force_ocr = False
    reasons: list[str] = []
    if scanned_pages >= max(1, total // 2):
        force_ocr = True
        reasons.append(f"扫描页占比高({scanned_pages}/{total})")
    if bad_pages >= max(1, total // 3):
        force_ocr = True
        reasons.append(f"低质量页占比高({bad_pages}/{total})")
    if (bad_pages + warning_pages) >= max(2, (2 * total) // 3):
        force_ocr = True
        reasons.append(f"质量告警页过多({bad_pages + warning_pages}/{total})")

    if force_ocr:
        note = f"{side}: 建议默认OCR，原因=" + "；".join(reasons)
    else:
        note = f"{side}: 文本层质量正常（抽样{total}页）"
    return force_ocr, note


def build_document_profile_task(pdf_path: str | Path) -> DocumentProfile:
    return build_document_profile(pdf_path)


def render_region_with_meta(
    pdf_path: str | Path,
    page_number: int,
    bbox: BBox,
    *,
    zoom: float = 3.0,
    padding: float = 0.0,
    grayscale: bool = False,
) -> RenderedRegion:
    return render_pdf_region_png_with_meta(
        Path(pdf_path),
        int(page_number),
        bbox,
        zoom=float(zoom),
        padding=float(padding),
        grayscale=bool(grayscale),
    )


def compute_prealign_payload(
    left_pdf: str | Path,
    right_pdf: str | Path,
    left_page_number: int,
    *,
    top_k_pages: int = 3,
    min_score: float = 0.05,
    top_k_regions: int = 2,
) -> PrealignComputationResult:
    left_doc = build_document_profile(left_pdf)
    right_doc = build_document_profile(right_pdf)
    candidates_map = retrieve_page_candidates(left_doc, right_doc, top_k=top_k_pages, min_score=min_score)
    page_candidates = candidates_map.get(int(left_page_number), [])
    if not page_candidates:
        return PrealignComputationResult(page_candidates=[], items_payload=[])

    left_page = parse_page(left_pdf, int(left_page_number))
    items_payload: list[tuple[int, int, BBox, BBox, float, str]] = []
    for pg in page_candidates:
        right_page = parse_page(right_pdf, int(pg.right_page))
        region_cands: list[RegionCandidate] = suggest_region_candidates(left_page, right_page, top_k=top_k_regions)
        for rc in region_cands:
            failure_text = {
                "anchor_sparse": "锚点稀疏",
                "scanned_noise": "扫描噪声",
                "layout_conflict": "版式冲突",
                "low_similarity": "相似度低",
                "ok": "正常",
            }.get(pg.failure_type, pg.failure_type)
            reason = (
                f"{rc.reason}; "
                f"页候选 score={pg.score:.2f} text_sim={pg.text_sim:.2f} "
                f"anchor_sim={pg.anchor_sim:.2f} failure={pg.failure_type}({failure_text})"
            )
            items_payload.append(
                (
                    int(left_page.page_number),
                    int(pg.right_page),
                    rc.left_bbox,
                    rc.right_bbox,
                    float(rc.score),
                    reason,
                )
            )
    return PrealignComputationResult(page_candidates=list(page_candidates), items_payload=items_payload)


def compute_visual_diff(
    left_pdf: str | Path,
    right_pdf: str | Path,
    left_page_number: int,
    right_page_number: int,
    left_bbox: BBox,
    right_bbox: BBox,
    *,
    zoom: float = 2.0,
    diff_threshold: int = 24,
) -> list[dict[str, object]]:
    return compute_visual_diff_payload(
        left_pdf,
        right_pdf,
        left_page_number,
        right_page_number,
        left_bbox,
        right_bbox,
        zoom=float(zoom),
        diff_threshold=int(diff_threshold),
    )


def sleep_task(seconds: float) -> float:
    import time

    time.sleep(float(seconds))
    return float(seconds)


def _sample_page_indices(page_count: int, max_samples: int = 5) -> list[int]:
    if page_count <= 0:
        return []
    if page_count <= max_samples:
        return list(range(page_count))
    positions = [0.0, 0.25, 0.5, 0.75, 1.0]
    idxs = sorted({min(page_count - 1, int(round((page_count - 1) * p))) for p in positions})
    return idxs[:max_samples]
