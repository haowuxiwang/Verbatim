from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.services.ocr_engines import LocalPaddleEngine


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Stress test local OCR timeout isolation")
    p.add_argument("--iterations", type=int, default=10)
    p.add_argument("--timeout-ms", type=int, default=600)
    p.add_argument("--worker-sleep-sec", type=float, default=2.0)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    os.environ["VERBATIM_LOCAL_OCR_ISOLATE"] = "1"
    os.environ["VERBATIM_OCR_WORKER_SLEEP_SEC"] = str(args.worker_sleep_sec)

    eng = LocalPaddleEngine(runtime_dir=None, offline_strict=False)
    ok_timeout = 0
    unexpected = 0
    durations: list[float] = []

    for i in range(1, int(args.iterations) + 1):
        t0 = time.monotonic()
        try:
            eng.recognize(
                image_bytes=b"x" * 512,
                filename=f"iter_{i}.png",
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
            durations.append(dt)
            print(f"[{i}] timeout as expected, elapsed={dt:.2f}s")
        except Exception as e:
            unexpected += 1
            print(f"[{i}] unexpected error: {e}")

    avg = (sum(durations) / len(durations)) if durations else 0.0
    print(
        f"SUMMARY iterations={args.iterations} expected_timeouts={ok_timeout} "
        f"unexpected={unexpected} avg_timeout_elapsed={avg:.2f}s"
    )

    # Pass condition: all iterations ended by timeout quickly.
    return 0 if ok_timeout == int(args.iterations) and unexpected == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
