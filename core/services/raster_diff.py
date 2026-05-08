from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops

from core.models import BBox
from core.ocr_client import render_pdf_region_png_with_meta


def compute_visual_diff_payload(
    left_pdf: str | Path,
    right_pdf: str | Path,
    left_page_number: int,
    right_page_number: int,
    left_bbox: BBox,
    right_bbox: BBox,
    *,
    zoom: float = 2.0,
    diff_threshold: int = 24,
) -> list[dict[str, Any]]:
    left_render = render_pdf_region_png_with_meta(
        Path(left_pdf),
        int(left_page_number),
        left_bbox,
        zoom=float(zoom),
        padding=0.0,
        grayscale=True,
    )
    right_render = render_pdf_region_png_with_meta(
        Path(right_pdf),
        int(right_page_number),
        right_bbox,
        zoom=float(zoom),
        padding=0.0,
        grayscale=True,
    )

    left_img = Image.open(BytesIO(left_render.image_bytes)).convert("L")
    right_img = Image.open(BytesIO(right_render.image_bytes)).convert("L")
    target_w = max(left_img.width, right_img.width)
    target_h = max(left_img.height, right_img.height)
    if target_w <= 1 or target_h <= 1:
        return []

    left_norm, left_scale, left_offset = _normalize_to_canvas(left_img, target_w, target_h)
    right_norm, right_scale, right_offset = _normalize_to_canvas(right_img, target_w, target_h)
    diff = ImageChops.difference(left_norm, right_norm)
    mask = diff.point(lambda p: 255 if p >= int(diff_threshold) else 0, mode="L")
    diff_bbox = mask.getbbox()
    if diff_bbox is None:
        return []

    diff_pixels = _count_nonzero(mask)
    total_pixels = target_w * target_h
    confidence = min(0.99, diff_pixels / float(max(1, total_pixels)))
    return [
        {
            "left_bbox": list(
                _map_canvas_bbox(diff_bbox, left_render.clip_bbox, left_scale, left_offset, left_img.size)
            ),
            "right_bbox": list(
                _map_canvas_bbox(diff_bbox, right_render.clip_bbox, right_scale, right_offset, right_img.size)
            ),
            "score": float(confidence),
            "diff_pixels": int(diff_pixels),
            "image_size": [int(target_w), int(target_h)],
        }
    ]


def _normalize_to_canvas(img: Image.Image, target_w: int, target_h: int) -> tuple[Image.Image, float, tuple[int, int]]:
    if img.width <= 0 or img.height <= 0:
        raise ValueError("invalid image size")
    scale = min(target_w / float(img.width), target_h / float(img.height))
    new_w = max(1, int(round(img.width * scale)))
    new_h = max(1, int(round(img.height * scale)))
    resized = img.resize((new_w, new_h))
    canvas = Image.new("L", (target_w, target_h), color=255)
    offset_x = (target_w - new_w) // 2
    offset_y = (target_h - new_h) // 2
    canvas.paste(resized, (offset_x, offset_y))
    return canvas, scale, (offset_x, offset_y)


def _map_canvas_bbox(
    bbox_px: tuple[int, int, int, int],
    clip_bbox: BBox,
    scale: float,
    offset: tuple[int, int],
    orig_size: tuple[int, int],
) -> BBox:
    ox, oy = offset
    x0 = max(0.0, (bbox_px[0] - ox) / max(scale, 1e-6))
    y0 = max(0.0, (bbox_px[1] - oy) / max(scale, 1e-6))
    x1 = min(float(orig_size[0]), (bbox_px[2] - ox) / max(scale, 1e-6))
    y1 = min(float(orig_size[1]), (bbox_px[3] - oy) / max(scale, 1e-6))
    cx0, cy0, cx1, cy1 = [float(v) for v in clip_bbox]
    clip_w = max(1.0, cx1 - cx0)
    clip_h = max(1.0, cy1 - cy0)
    mapped = (
        cx0 + (x0 / max(1.0, float(orig_size[0]))) * clip_w,
        cy0 + (y0 / max(1.0, float(orig_size[1]))) * clip_h,
        cx0 + (x1 / max(1.0, float(orig_size[0]))) * clip_w,
        cy0 + (y1 / max(1.0, float(orig_size[1]))) * clip_h,
    )
    return (
        max(cx0, min(cx1, mapped[0])),
        max(cy0, min(cy1, mapped[1])),
        max(cx0, min(cx1, mapped[2])),
        max(cy0, min(cy1, mapped[3])),
    )


def _count_nonzero(mask: Image.Image) -> int:
    hist = mask.histogram()
    if len(hist) < 256:
        return 0
    return int(sum(hist[1:]))
