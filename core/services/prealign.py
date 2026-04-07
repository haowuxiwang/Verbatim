from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from core.layout_analyzer import LayoutInfo, LayoutType, detect_layout
from core.models import BBox, CharData, PageData
from core.pdf_parser import parse_page
from core.services.text_quality import check_text_quality


@dataclass(frozen=True)
class PageProfile:
    page_number: int
    char_count: int
    quality: str
    confidence: int
    layout_type: str
    is_scanned: bool
    signature: frozenset[str]
    anchors: frozenset[str]
    text_sample: str


@dataclass(frozen=True)
class DocumentProfile:
    pdf_path: str
    page_count: int
    pages: list[PageProfile]
    scan_ratio: float
    bad_ratio: float
    two_column_ratio: float


@dataclass(frozen=True)
class PageCandidate:
    left_page: int
    right_page: int
    score: float
    text_sim: float
    anchor_sim: float
    layout_bonus: float
    quality_bonus: float
    failure_type: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RegionCandidate:
    left_bbox: BBox
    right_bbox: BBox
    score: float
    reason: str


def _normalize_for_signature(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"\s+", "", t)
    t = re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", t)
    return t


def _char_ngrams(text: str, n: int = 2, limit: int = 1000) -> frozenset[str]:
    t = _normalize_for_signature(text)
    if not t:
        return frozenset()
    if len(t) <= n:
        return frozenset([t])
    grams: list[str] = []
    for i in range(len(t) - n + 1):
        grams.append(t[i : i + n])
        if len(grams) >= limit:
            break
    return frozenset(grams)


def _page_text(page: PageData, max_chars: int = 12000) -> str:
    if not page.text_chars:
        return ""
    return "".join(ch.char for ch in page.text_chars)[:max_chars]


def _group_chars_by_line(chars: list[CharData]) -> list[list[CharData]]:
    if not chars:
        return []
    heights = sorted([(c.bbox[3] - c.bbox[1]) for c in chars if c.bbox[3] > c.bbox[1]])
    median_h = heights[len(heights) // 2] if heights else 8.0
    threshold = max(2.0, median_h * 0.75)
    ordered = sorted(chars, key=lambda c: (c.bbox[1], c.bbox[0]))
    lines: list[list[CharData]] = []
    cur: list[CharData] = []
    cur_y: float | None = None
    for ch in ordered:
        y = ch.bbox[1]
        if cur_y is None or abs(y - cur_y) <= threshold:
            cur.append(ch)
            if cur_y is None:
                cur_y = y
            else:
                cur_y = (cur_y * (len(cur) - 1) + y) / len(cur)
        else:
            lines.append(cur)
            cur = [ch]
            cur_y = y
    if cur:
        lines.append(cur)
    return lines


def _robust_layout_type(page: PageData) -> str:
    base: LayoutInfo = detect_layout(page.text_chars, page.width)
    layout_type = base.layout_type.value if isinstance(base.layout_type, LayoutType) else str(base.layout_type)
    if layout_type != "two_column" or len(page.text_chars) < 40:
        return layout_type

    boundary = float(base.column_boundaries[0]) if base.column_boundaries else (float(page.width) * 0.5)
    lines = _group_chars_by_line(page.text_chars)
    if not lines:
        return layout_type

    cross = 0
    left_only = 0
    right_only = 0
    for ln in lines:
        has_left = any(((c.bbox[0] + c.bbox[2]) * 0.5) < boundary for c in ln)
        has_right = any(((c.bbox[0] + c.bbox[2]) * 0.5) >= boundary for c in ln)
        if has_left and has_right:
            cross += 1
        elif has_left:
            left_only += 1
        elif has_right:
            right_only += 1
    total = max(1, len(lines))
    cross_ratio = cross / total
    left_ratio = left_only / total
    right_ratio = right_only / total

    if cross_ratio >= 0.42:
        return "single"
    if min(left_ratio, right_ratio) < 0.12:
        return "single"
    return "two_column"


def _extract_anchor_tokens(text: str, *, max_tokens: int = 80) -> frozenset[str]:
    t = (text or "").strip()
    if not t:
        return frozenset()

    anchors: list[str] = []
    for p in [
        r"第[一二三四五六七八九十百零0-9]+[章节条款]",
        r"\b\d{1,2}(?:\.\d{1,2}){0,3}\b",
        r"[A-Za-z]{2,}\d{0,2}",
    ]:
        anchors.extend(re.findall(p, t))

    anchors.extend(re.findall(r"[\u4e00-\u9fff]{2,8}", t))

    seen: set[str] = set()
    out: list[str] = []
    for a in anchors:
        k = a.strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(k)
        if len(out) >= max_tokens:
            break
    return frozenset(out)


def profile_page(page: PageData) -> PageProfile:
    text = _page_text(page)
    quality = check_text_quality(text)
    layout_type = _robust_layout_type(page)
    return PageProfile(
        page_number=page.page_number,
        char_count=len(text),
        quality=str(quality.get("quality", "good")),
        confidence=int(quality.get("confidence", 0)),
        layout_type=layout_type,
        is_scanned=(len(text.strip()) == 0),
        signature=_char_ngrams(text, n=2),
        anchors=_extract_anchor_tokens(text),
        text_sample=text[:80],
    )


def build_document_profile(pdf_path: str | Path) -> DocumentProfile:
    p = Path(pdf_path)
    import fitz

    with fitz.open(p) as doc:
        page_count = int(doc.page_count)

    pages: list[PageProfile] = []
    for i in range(page_count):
        page = parse_page(p, i)
        pages.append(profile_page(page))

    if not pages:
        return DocumentProfile(
            pdf_path=str(p),
            page_count=0,
            pages=[],
            scan_ratio=0.0,
            bad_ratio=0.0,
            two_column_ratio=0.0,
        )

    total = len(pages)
    scan_pages = sum(1 for pg in pages if pg.is_scanned)
    bad_pages = sum(1 for pg in pages if pg.quality == "bad")
    two_column_pages = sum(1 for pg in pages if pg.layout_type == "two_column")
    return DocumentProfile(
        pdf_path=str(p),
        page_count=page_count,
        pages=pages,
        scan_ratio=scan_pages / total,
        bad_ratio=bad_pages / total,
        two_column_ratio=two_column_pages / total,
    )


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a.intersection(b))
    union = len(a.union(b))
    if union <= 0:
        return 0.0
    return inter / union


def score_page_pair(left: PageProfile, right: PageProfile) -> PageCandidate:
    text_sim = _jaccard(left.signature, right.signature)
    anchor_sim = _jaccard(left.anchors, right.anchors)
    layout_bonus = 0.08 if left.layout_type == right.layout_type else -0.02
    quality_bonus = 0.0
    reasons: list[str] = []

    if left.quality == "good" and right.quality == "good":
        quality_bonus += 0.10
        reasons.append("双侧文本层质量较好")
    elif left.quality == "bad" and right.quality == "bad":
        quality_bonus -= 0.05
        reasons.append("双侧质量较差，建议依赖OCR/人工确认")

    if left.is_scanned or right.is_scanned:
        quality_bonus -= 0.05
        reasons.append("至少一侧疑似扫描页")

    score = text_sim * 0.55 + anchor_sim * 0.30 + layout_bonus + quality_bonus
    score = max(0.0, min(1.0, score))

    if text_sim >= 0.35:
        reasons.append("文本签名相似度较高")
    elif text_sim <= 0.05:
        reasons.append("文本签名相似度很低")

    if anchor_sim >= 0.20:
        reasons.append("锚点特征匹配较高")
    elif anchor_sim <= 0.03:
        reasons.append("锚点特征不足")

    if left.layout_type == right.layout_type:
        reasons.append(f"布局一致({left.layout_type})")
    else:
        reasons.append(f"布局不同({left.layout_type} vs {right.layout_type})")

    failure_type = "ok"
    if left.is_scanned or right.is_scanned:
        failure_type = "scanned_noise"
    elif left.layout_type != right.layout_type and score < 0.2:
        failure_type = "layout_conflict"
    elif anchor_sim < 0.03 and text_sim < 0.05:
        failure_type = "anchor_sparse"
    elif score < 0.12:
        failure_type = "low_similarity"

    return PageCandidate(
        left_page=left.page_number,
        right_page=right.page_number,
        score=score,
        text_sim=text_sim,
        anchor_sim=anchor_sim,
        layout_bonus=layout_bonus,
        quality_bonus=quality_bonus,
        failure_type=failure_type,
        reasons=tuple(reasons),
    )


def retrieve_page_candidates(
    left_doc: DocumentProfile,
    right_doc: DocumentProfile,
    *,
    top_k: int = 3,
    min_score: float = 0.08,
) -> dict[int, list[PageCandidate]]:
    out: dict[int, list[PageCandidate]] = {}
    right_pages_total = max(1, right_doc.page_count)
    left_pages_total = max(1, left_doc.page_count)

    for left_idx, left_page in enumerate(left_doc.pages):
        scored = [score_page_pair(left_page, rp) for rp in right_doc.pages]
        if not scored:
            out[left_page.page_number] = []
            continue

        left_pos = left_idx / max(1, left_pages_total - 1)

        def _rank(c: PageCandidate) -> float:
            # Position prior:
            # in extreme N-vs-small-M docs, this helps retain plausible page-order
            # continuity and avoids over-concentrating all left pages to one right page.
            right_pos = c.right_page / max(1, right_pages_total - 1)
            pos_bonus = 0.08 * (1.0 - min(1.0, abs(left_pos - right_pos)))
            return c.score + pos_bonus

        scored.sort(key=_rank, reverse=True)

        max_keep = max(1, top_k)
        chosen = [c for c in scored if c.score >= min_score][:max_keep]

        # Diversity fallback: when right-side pages are very few (e.g. 17 vs 2),
        # keep at least one candidate per distinct right page where possible.
        if len(chosen) < min(max_keep, len(scored)):
            used = {c.right_page for c in chosen}
            for c in scored:
                if len(chosen) >= max_keep:
                    break
                if c.right_page in used:
                    continue
                chosen.append(c)
                used.add(c.right_page)

        if not chosen:
            chosen = [scored[0]]
        out[left_page.page_number] = chosen
    return out


def _union_bbox(chars: list[CharData]) -> BBox:
    if not chars:
        return (0.0, 0.0, 0.0, 0.0)
    x0 = min(c.bbox[0] for c in chars)
    y0 = min(c.bbox[1] for c in chars)
    x1 = max(c.bbox[2] for c in chars)
    y1 = max(c.bbox[3] for c in chars)
    return (float(x0), float(y0), float(x1), float(y1))


def _expand_bbox(b: BBox, *, page_w: float, page_h: float) -> BBox:
    x0, y0, x1, y1 = b
    w = max(1.0, x1 - x0)
    h = max(1.0, y1 - y0)
    pad_x = max(8.0, min(page_w * 0.08, w * 0.8))
    pad_y = max(4.0, min(page_h * 0.04, h * 0.8))
    nx0 = max(0.0, x0 - pad_x)
    ny0 = max(0.0, y0 - pad_y)
    nx1 = min(float(page_w), x1 + pad_x)
    ny1 = min(float(page_h), y1 + pad_y)
    return (nx0, ny0, nx1, ny1)


def _anchor_bbox(page: PageData, anchor: str) -> BBox | None:
    if not anchor or not page.text_chars:
        return None
    text = "".join(c.char for c in page.text_chars)
    start = text.find(anchor)
    if start < 0:
        return None
    end = start + len(anchor)
    seg = page.text_chars[start:end]
    if not seg:
        return None
    return _union_bbox(seg)


def suggest_region_candidates(
    left_page: PageData,
    right_page: PageData,
    *,
    top_k: int = 3,
) -> list[RegionCandidate]:
    left_prof = profile_page(left_page)
    right_prof = profile_page(right_page)
    shared = sorted(left_prof.anchors.intersection(right_prof.anchors), key=len, reverse=True)
    out: list[RegionCandidate] = []
    for a in shared[:10]:
        lb = _anchor_bbox(left_page, a)
        rb = _anchor_bbox(right_page, a)
        if lb is None or rb is None:
            continue
        lb2 = _expand_bbox(lb, page_w=left_page.width, page_h=left_page.height)
        rb2 = _expand_bbox(rb, page_w=right_page.width, page_h=right_page.height)
        s = min(0.95, 0.55 + min(0.30, len(a) / 30.0))
        out.append(RegionCandidate(left_bbox=lb2, right_bbox=rb2, score=s, reason=f"锚点匹配: {a}"))
        if len(out) >= top_k:
            break

    if out:
        return out

    # Fallback: centered broad regions to reduce user manual effort.
    lw, lh = float(left_page.width), float(left_page.height)
    rw, rh = float(right_page.width), float(right_page.height)
    left_fb = (lw * 0.12, lh * 0.22, lw * 0.88, lh * 0.62)
    right_fb = (rw * 0.12, rh * 0.22, rw * 0.88, rh * 0.62)
    return [
        RegionCandidate(
            left_bbox=left_fb,
            right_bbox=right_fb,
            score=0.20,
            reason="锚点不足，返回兜底候选区域（建议人工微调）",
        )
    ]
