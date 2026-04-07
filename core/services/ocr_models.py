from __future__ import annotations

from dataclasses import dataclass

from core.models import BBox


@dataclass(frozen=True)
class OcrSpan:
    text: str
    bbox: BBox


@dataclass(frozen=True)
class OcrResult:
    text: str
    raw_text: str
    spans: tuple[OcrSpan, ...]


@dataclass(frozen=True)
class OcrResultMeta:
    engine: str
    mode: str
    route: str
    side: str
    variant: int
    bbox: BBox
    clip_bbox: BBox | None
    zoom: float
    image_bytes: int
