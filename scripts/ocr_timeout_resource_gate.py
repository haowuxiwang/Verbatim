from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
    p = argparse.ArgumentParser(description="OCR timeout stress with resource gate")
    p.add_argument("--iterations", type=int, default=30)
    p.add_argument("--timeout-ms", type=int, default=700)
    p.add_argument("--worker-sleep-sec", type=float, default=2.5)
    p.add_argument("--max-rss-delta-mb", type=float, default=200.0)
    p.add_argument("--max-handle-delta", type=int, default=300)
    p.add_argument("--recovery-check", action="store_true", help="perform a post-timeout recovery attempt")
    p.add_argument("--recovery-timeout-ms", type=int, default=5000)
    p.add_argument("--recovery-sleep-sec", type=float, default=0.0)
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
    if not psapi.GetProcessMemoryInfo(h_proc, ctypes.byref(pmc), pmc.cb):
        raise RuntimeError("GetProcessMemoryInfo failed")

    h_count = ctypes.c_ulong()
    if not k32.GetProcessHandleCount(h_proc, ctypes.byref(h_count)):
        raise RuntimeError("GetProcessHandleCount failed")

    rss_mb = float(pmc.WorkingSetSize) / (1024.0 * 1024.0)
    return rss_mb, int(h_count.value)


def main() -> int:
    args = parse_args()
    os.environ["VERBATIM_LOCAL_OCR_ISOLATE"] = "1"
    os.environ["VERBATIM_OCR_WORKER_SLEEP_SEC"] = str(args.worker_sleep_sec)

    eng = LocalPaddleEngine(runtime_dir=None, offline_strict=False)
    start_rss, start_handles = _proc_metrics()

    ok_timeout = 0
    unexpected = 0
    elapsed_list: list[float] = []

    for i in range(1, int(args.iterations) + 1):
        t0 = time.monotonic()
        try:
            eng.recognize(
                image_bytes=b"x" * 1024,
                filename=f"gate_{i}.png",
                mode="sync",
                run_bg=lambda fn, *a, **k: fn(*a, **k),
                timeout_ms=int(args.timeout_ms),
                allow_sync_to_async_retry=False,
            )
            unexpected += 1
            print(f"[{i}] unexpected success")
        except TimeoutError:
            ok_timeout += 1
            dt = time.monotonic() - t0
            elapsed_list.append(dt)
            print(f"[{i}] timeout ok, elapsed={dt:.2f}s")
        except Exception as e:
            unexpected += 1
            print(f"[{i}] unexpected error: {e}")

    end_rss, end_handles = _proc_metrics()
    rss_delta = end_rss - start_rss
    handle_delta = end_handles - start_handles
    avg_elapsed = (sum(elapsed_list) / len(elapsed_list)) if elapsed_list else 0.0

    recovery_ok = True
    if args.recovery_check:
        os.environ["VERBATIM_OCR_WORKER_SLEEP_SEC"] = str(args.recovery_sleep_sec)
        try:
            eng.recognize(
                image_bytes=b"x" * 1024,
                filename="recovery.png",
                mode="sync",
                run_bg=lambda fn, *a, **k: fn(*a, **k),
                timeout_ms=int(args.recovery_timeout_ms),
                allow_sync_to_async_retry=False,
            )
        except Exception:
            recovery_ok = False

    gate_pass = (
        ok_timeout == int(args.iterations)
        and unexpected == 0
        and rss_delta <= float(args.max_rss_delta_mb)
        and handle_delta <= int(args.max_handle_delta)
        and (recovery_ok if args.recovery_check else True)
    )

    print(
        "SUMMARY "
        f"iterations={args.iterations} expected_timeouts={ok_timeout} unexpected={unexpected} "
        f"avg_elapsed={avg_elapsed:.2f}s rss_start={start_rss:.1f}MB rss_end={end_rss:.1f}MB "
        f"rss_delta={rss_delta:.1f}MB handles_start={start_handles} handles_end={end_handles} "
        f"handle_delta={handle_delta} recovery_ok={recovery_ok} gate_pass={gate_pass}"
    )
    return 0 if gate_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
