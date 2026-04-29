from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Mapping
from numbers import Real
from pathlib import Path

from .ocr_engines import LocalPaddleEngine


def _normalize_box(raw_box) -> tuple[float, float, float, float] | None:
    if raw_box is not None and not isinstance(raw_box, (list, tuple)) and hasattr(raw_box, "tolist"):
        try:
            raw_box = raw_box.tolist()
        except Exception:
            pass
    if not isinstance(raw_box, (list, tuple)) or not raw_box:
        return None
    if len(raw_box) == 4 and all(isinstance(x, Real) for x in raw_box):
        x0, y0, x1, y1 = [float(x) for x in raw_box]
        return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    if all(isinstance(p, (list, tuple)) and len(p) >= 2 for p in raw_box):
        xs = [float(p[0]) for p in raw_box]
        ys = [float(p[1]) for p in raw_box]
        return (min(xs), min(ys), max(xs), max(ys))
    return None


def _pick_boxes(*candidates):
    for c in candidates:
        if c is None:
            continue
        try:
            if len(c) > 0:
                return c
        except Exception:
            continue
    return None


def _extract_spans_from_output(obj) -> list[dict]:
    spans: list[dict] = []
    if obj is None:
        return spans

    if isinstance(obj, Mapping):
        rec_texts = obj.get("rec_texts")
        dt_boxes = obj.get("dt_boxes")
        rec_boxes = obj.get("rec_boxes")
        rec_polys = obj.get("rec_polys")
        dt_polys = obj.get("dt_polys")
        boxes = _pick_boxes(dt_boxes, rec_boxes, rec_polys, dt_polys)
        if isinstance(rec_texts, list) and boxes is not None:
            try:
                boxes_iter = list(boxes)
            except Exception:
                boxes_iter = []
            for text, box in zip(rec_texts, boxes_iter, strict=False):
                if not isinstance(text, str):
                    continue
                bbox = _normalize_box(box)
                if bbox is None:
                    continue
                t = text.strip()
                if t:
                    spans.append({"text": t, "bbox": bbox})

        text = obj.get("text")
        raw_box = obj.get("box") or obj.get("bbox") or obj.get("boxes")
        if isinstance(text, str) and raw_box is not None:
            bbox = _normalize_box(raw_box)
            if bbox is not None:
                t = text.strip()
                if t:
                    spans.append({"text": t, "bbox": bbox})

        for v in obj.values():
            spans.extend(_extract_spans_from_output(v))
        return spans

    # Support PaddleX OCRResult objects (attribute-based).
    if not isinstance(obj, (list, tuple)) and not isinstance(obj, dict):
        attrs = {}
        for key in (
            "rec_texts",
            "rec_boxes",
            "dt_boxes",
            "dt_polys",
            "rec_polys",
            "text",
            "box",
            "bbox",
            "boxes",
        ):
            if hasattr(obj, key):
                attrs[key] = getattr(obj, key)
        if attrs:
            return _extract_spans_from_output(attrs)

    if isinstance(obj, (list, tuple)):
        if len(obj) == 2:
            raw_box, raw_text = obj[0], obj[1]
            bbox = _normalize_box(raw_box)
            text = None
            if isinstance(raw_text, str):
                text = raw_text
            elif isinstance(raw_text, (list, tuple)) and raw_text:
                if isinstance(raw_text[0], str):
                    text = raw_text[0]
            elif isinstance(raw_text, dict):
                if isinstance(raw_text.get("text"), str):
                    text = raw_text.get("text")
            if bbox is not None and text:
                t = text.strip()
                if t:
                    spans.append({"text": t, "bbox": bbox})
                return spans

        for it in obj:
            spans.extend(_extract_spans_from_output(it))
        return spans

    return spans


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Local OCR isolated worker")
    p.add_argument("--image", default="", help="Path to rendered PNG image")
    p.add_argument("--runtime-dir", default="", help="OCR runtime directory")
    p.add_argument("--offline-strict", default="1", help="1/0 strict offline checks")
    p.add_argument("--self-check", action="store_true", help="Validate worker bootstrap and OCR runtime")
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    image_raw = str(args.image or "").strip()
    image_path = Path(image_raw).resolve() if image_raw else None
    runtime_raw = str(args.runtime_dir or "").strip()
    runtime_dir = Path(runtime_raw).resolve() if runtime_raw else None
    offline_strict = str(args.offline_strict).strip().lower() in {"1", "true", "yes", "on"}

    try:
        sleep_sec = float(os.getenv("VERBATIM_OCR_WORKER_SLEEP_SEC", "0") or "0")
        if sleep_sec > 0:
            time.sleep(sleep_sec)
        eng = LocalPaddleEngine(runtime_dir=runtime_dir, offline_strict=offline_strict)
        ocr = eng._ensure_ocr()
        if args.self_check:
            print(json.dumps(LocalPaddleEngine._self_check_success_payload(), ensure_ascii=False))
            return 0
        if image_path is None:
            print(json.dumps({"ok": False, "error": "missing --image for OCR worker run"}, ensure_ascii=False))
            return 2
        output = ocr.predict(str(image_path))
        text = eng._extract_local_text(output)
        spans = _extract_spans_from_output(output)
        if str(os.getenv("VERBATIM_DEBUG_SPANS_TRACE", "0")).strip().lower() in {"1", "true", "yes", "on"}:
            try:
                keys = sorted(output.keys()) if isinstance(output, dict) else []
                sys.stderr.write(
                    f"[worker] spans_count={len(spans)} keys={keys}\n"
                )
            except Exception:
                pass
        print(json.dumps({"ok": True, "text": text, "spans": spans}, ensure_ascii=False))
        return 0
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
