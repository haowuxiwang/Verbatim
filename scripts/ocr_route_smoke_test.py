#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.models import BBox
from core.ocr_client import OcrConfig, PaddleOcrClient, render_pdf_region_png
from core.services.ocr_engines import CloudPaddleEngine, LocalPaddleEngine
from core.services.text_quality import check_text_quality


def parse_bbox(raw: str) -> BBox:
    parts = [x.strip() for x in (raw or "").split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be x0,y0,x1,y1")
    vals = [float(x) for x in parts]
    return (vals[0], vals[1], vals[2], vals[3])


def run_with_timeout(fn, *args, timeout_ms: int = 15000, **kwargs):
    holder: dict[str, Any] = {"result": None, "error": None}
    done = threading.Event()

    def _target() -> None:
        try:
            holder["result"] = fn(*args, **kwargs)
        except Exception as e:
            holder["error"] = e
        finally:
            done.set()

    t = threading.Thread(target=_target, daemon=True, name="ocr-smoke-bg")
    t.start()
    if not done.wait(max(1, int(timeout_ms)) / 1000.0):
        raise TimeoutError(f"background call timed out >{timeout_ms}ms")
    if holder["error"] is not None:
        raise holder["error"]
    return holder["result"]


def resolve_route(route: str) -> str:
    s = (route or "").strip().lower()
    if s in {"local_first", "cloud_only", "local_only"}:
        return s
    return "local_first"


def resolve_engines(route: str):
    engines: list[tuple[str, object]] = []
    strict_offline = str(os.getenv("VERBATIM_OCR_OFFLINE_STRICT", "1")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    local = LocalPaddleEngine(runtime_dir=runtime_dir_from_env(), offline_strict=strict_offline)
    cloud = cloud_engine_from_env()
    if route == "cloud_only":
        if cloud is not None:
            engines.append(("cloud", cloud))
        return engines
    if route == "local_only":
        engines.append(("local", local))
        return engines
    engines.append(("local", local))
    if cloud is not None:
        engines.append(("cloud", cloud))
    return engines


def runtime_dir_from_env() -> Path | None:
    raw = (os.getenv("VERBATIM_OCR_RUNTIME_DIR") or "").strip()
    if raw:
        p = Path(raw)
        return p if p.exists() else None
    local_default = Path.cwd() / "ocr_runtime"
    return local_default if local_default.exists() else None


def cloud_engine_from_env() -> CloudPaddleEngine | None:
    cfg = OcrConfig.load()
    if cfg is None:
        return None
    return CloudPaddleEngine(PaddleOcrClient(cfg))


def main() -> int:
    ap = argparse.ArgumentParser(description="OCR route smoke test for Verbatim")
    ap.add_argument("--pdf", required=True, help="PDF file path")
    ap.add_argument("--page", type=int, default=0, help="0-based page index")
    ap.add_argument("--bbox", required=True, help="x0,y0,x1,y1 in PDF coordinates")
    ap.add_argument("--repeat", type=int, default=2, help="repeat count")
    ap.add_argument("--route", default=os.getenv("VERBATIM_OCR_ROUTE", "local_first"))
    ap.add_argument("--ocr-mode", default="sync", choices=["sync", "async"])
    ap.add_argument("--timeout-ms", type=int, default=15000)
    ap.add_argument("--zoom", type=float, default=3.0)
    ap.add_argument("--padding", type=float, default=2.0)
    args = ap.parse_args()

    pdf = Path(args.pdf)
    if not pdf.exists():
        raise FileNotFoundError(f"pdf not found: {pdf}")
    bbox = parse_bbox(args.bbox)
    route = resolve_route(args.route)
    engines = resolve_engines(route)
    if not engines:
        print(
            json.dumps(
                {
                    "ok": False,
                    "route": route,
                    "error": "no engine resolved (missing cloud token and/or local runtime)",
                },
                ensure_ascii=False,
            )
        )
        return 2

    stats: dict[str, Any] = {
        "ok": True,
        "route": route,
        "repeat": int(max(1, args.repeat)),
        "engines": [name for name, _ in engines],
        "runs": [],
        "fallback_count": 0,
        "success_count": 0,
        "fail_count": 0,
    }
    started = time.time()
    for i in range(int(max(1, args.repeat))):
        run_item: dict[str, Any] = {"index": i + 1, "attempts": [], "success": False}
        image_bytes = render_pdf_region_png(
            pdf_path=pdf,
            page_number=args.page,
            bbox=bbox,
            zoom=float(args.zoom),
            padding=float(args.padding),
            grayscale=False,
        )
        for idx, (engine_name, engine) in enumerate(engines):
            attempt: dict[str, Any] = {"engine": engine_name}
            try:
                out = engine.recognize(
                    image_bytes=image_bytes,
                    filename=f"smoke_{i + 1}.png",
                    mode=args.ocr_mode,
                    run_bg=run_with_timeout,
                    timeout_ms=int(args.timeout_ms),
                    allow_sync_to_async_retry=True,
                )
                text = (out.text or "").strip()
                q = check_text_quality(text)
                attempt.update(
                    {
                        "mode": str(out.mode),
                        "text_len": len(text),
                        "quality": q.get("quality", "unknown"),
                        "confidence": int(q.get("confidence", 0) or 0),
                    }
                )
                run_item["attempts"].append(attempt)
                run_item["success"] = True
                run_item["engine"] = engine_name
                if idx > 0:
                    stats["fallback_count"] += 1
                stats["success_count"] += 1
                break
            except Exception as e:
                attempt["error"] = str(e)
                run_item["attempts"].append(attempt)
                continue
        if not run_item["success"]:
            stats["fail_count"] += 1
        stats["runs"].append(run_item)

    stats["elapsed_sec"] = round(time.time() - started, 3)
    stats["availability"] = round(stats["success_count"] / max(1, stats["repeat"]), 3)
    stats["trustworthy"] = bool(stats["availability"] >= 0.8 and stats["fail_count"] == 0)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0 if stats["success_count"] > 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
