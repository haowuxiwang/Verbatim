from __future__ import annotations

import argparse
import ctypes
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.diff_regions import diff_regions
from core.ocr_client import render_pdf_region_png
from core.pdf_parser import parse_page
from core.region_extractor import extract_region
from core.sample_assets import resolve_sample_path
from core.services.ocr_engines import LocalPaddleEngine


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Real compare stress (mixed OCR on/off)")
    p.add_argument("--left-pdf", default="original.pdf")
    p.add_argument("--right-pdf", default="digest.pdf")
    p.add_argument("--left-page", type=int, default=0)
    p.add_argument("--right-page", type=int, default=0)
    p.add_argument("--iterations", type=int, default=30)
    p.add_argument("--ocr-timeout-ms", type=int, default=30000)
    p.add_argument("--local-ocr-isolate", type=int, choices=[0, 1], default=1)
    p.add_argument("--max-rss-delta-mb", type=float, default=250.0)
    p.add_argument("--max-handle-delta", type=int, default=400)
    p.add_argument("--max-p95-sec", type=float, default=10.0)
    p.add_argument("--skip-ocr", action="store_true", help="skip OCR path entirely")
    return p.parse_args()


def _proc_metrics() -> tuple[float, int]:
    k32 = ctypes.windll.kernel32
    psapi = ctypes.windll.psapi
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    k32.GetProcessHandleCount.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    k32.GetProcessHandleCount.restype = ctypes.c_int
    h_proc = k32.GetCurrentProcess()

    pmc = PROCESS_MEMORY_COUNTERS_EX()
    pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
    ok_mem = psapi.GetProcessMemoryInfo(h_proc, ctypes.byref(pmc), pmc.cb)
    if not ok_mem:
        raise RuntimeError("GetProcessMemoryInfo failed")

    h_count = ctypes.c_ulong()
    ok_handle = k32.GetProcessHandleCount(h_proc, ctypes.byref(h_count))
    if not ok_handle:
        raise RuntimeError("GetProcessHandleCount failed")

    rss_mb = float(pmc.WorkingSetSize) / (1024.0 * 1024.0)
    return rss_mb, int(h_count.value)


def _default_bbox(page, *, x0=0.06, y0=0.16, x1=0.78, y1=0.62):
    return (
        float(page.width) * x0,
        float(page.height) * y0,
        float(page.width) * x1,
        float(page.height) * y1,
    )


def _run_local_ocr_once(
    eng: LocalPaddleEngine, pdf_path: Path, page_no: int, bbox: tuple[float, float, float, float], timeout_ms: int
) -> tuple[str, str, str]:
    try:
        image_bytes = render_pdf_region_png(
            pdf_path,
            page_no,
            bbox,
            zoom=3.0,
            padding=2.0,
            grayscale=False,
        )
        out = eng.recognize(
            image_bytes=image_bytes,
            filename="stress_region.png",
            mode="sync",
            run_bg=lambda fn, *a, **k: fn(*a, **k),
            timeout_ms=timeout_ms,
            allow_sync_to_async_retry=False,
        )
        text = (out.text or "").strip()
        if not text:
            return "", "empty_text", "ocr_text_empty"
        return text, "", ""
    except TimeoutError:
        return "", "timeout", "timeout"
    except Exception as e:
        return "", "ocr_error", str(e)


def main() -> int:
    args = parse_args()
    os.environ["VERBATIM_LOCAL_OCR_ISOLATE"] = str(int(args.local_ocr_isolate))
    os.environ.pop("VERBATIM_OCR_WORKER_SLEEP_SEC", None)

    left_pdf = resolve_sample_path(args.left_pdf)
    right_pdf = resolve_sample_path(args.right_pdf)
    if not left_pdf.exists() or not right_pdf.exists():
        print("ERROR missing input PDFs")
        return 2

    left_page = parse_page(left_pdf, int(args.left_page))
    right_page = parse_page(right_pdf, int(args.right_page))
    left_bbox = _default_bbox(left_page)
    right_bbox = _default_bbox(right_page)
    base_left_region = extract_region(
        left_page,
        [left_bbox],
        strict_bounds=True,
        reading_order_mode="raw",
    )
    base_right_region = extract_region(
        right_page,
        [right_bbox],
        strict_bounds=True,
        reading_order_mode="raw",
    )

    start_rss, start_handles = _proc_metrics()
    runtime_dir = Path.cwd() / "ocr_runtime"
    eng = LocalPaddleEngine(runtime_dir=(runtime_dir if runtime_dir.exists() else None), offline_strict=False)

    failures = {"timeout": 0, "ocr_error": 0, "empty_text": 0, "diff_error": 0}
    failure_samples: list[str] = []
    durations: list[float] = []
    with_ocr = 0
    without_ocr = 0
    success = 0

    for i in range(1, int(args.iterations) + 1):
        t0 = time.monotonic()
        use_ocr = (i % 2 == 0) and (not args.skip_ocr)
        if use_ocr:
            with_ocr += 1
        else:
            without_ocr += 1

        try:
            left_region = base_left_region
            right_region = base_right_region

            if use_ocr:
                ocr_text, reason, detail = _run_local_ocr_once(
                    eng, right_pdf, int(args.right_page), right_bbox, int(args.ocr_timeout_ms)
                )
                if reason:
                    failures[reason] += 1
                    if len(failure_samples) < 5:
                        failure_samples.append(f"{reason}:{detail}")
                else:
                    right_region = type(right_region)(
                        page_number=right_region.page_number,
                        bboxes=right_region.bboxes,
                        chars=right_region.chars,
                    )
                    # Keep compare real-chain; OCR result is only used to stress OCR path.
                    _ = ocr_text

            ops, _ = diff_regions(
                left_region,
                right_region,
                pure_content_mode=True,
                ignore_punctuation=True,
                normalize_numbers=True,
                merge_key_value_lines=True,
            )
            _ = len(ops)
            success += 1
        except Exception:
            failures["diff_error"] += 1

        dt = time.monotonic() - t0
        durations.append(dt)
        print(f"[iter {i:02d}] use_ocr={use_ocr} elapsed={dt:.2f}s")

    end_rss, end_handles = _proc_metrics()
    rss_delta = end_rss - start_rss
    handle_delta = end_handles - start_handles
    p50 = statistics.median(durations) if durations else 0.0
    p95 = sorted(durations)[max(0, int(len(durations) * 0.95) - 1)] if durations else 0.0
    fail_total = sum(failures.values())

    p95_pass = p95 <= float(args.max_p95_sec)
    gate_pass = (
        success == int(args.iterations)
        and fail_total == 0
        and rss_delta <= float(args.max_rss_delta_mb)
        and handle_delta <= int(args.max_handle_delta)
        and p95_pass
    )

    # Recovery check: after stress loop, ensure a plain diff still runs.
    recovery_ok = True
    try:
        _ = diff_regions(
            base_left_region,
            base_right_region,
            pure_content_mode=True,
            ignore_punctuation=True,
            normalize_numbers=True,
            merge_key_value_lines=True,
        )
    except Exception:
        recovery_ok = False

    summary = {
        "iterations": int(args.iterations),
        "success": success,
        "with_ocr": with_ocr,
        "without_ocr": without_ocr,
        "failures": failures,
        "failure_samples": failure_samples,
        "duration_sec": {
            "p50": round(p50, 3),
            "p95": round(p95, 3),
            "avg": round(sum(durations) / len(durations), 3) if durations else 0.0,
        },
        "resource": {
            "rss_start_mb": round(start_rss, 2),
            "rss_end_mb": round(end_rss, 2),
            "rss_delta_mb": round(rss_delta, 2),
            "handles_start": start_handles,
            "handles_end": end_handles,
            "handle_delta": handle_delta,
        },
        "p95_gate_sec": float(args.max_p95_sec),
        "p95_pass": bool(p95_pass),
        "recovery_ok": bool(recovery_ok),
        "gate_pass": gate_pass,
    }
    print("SUMMARY_JSON " + json.dumps(summary, ensure_ascii=False))
    return 0 if gate_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
