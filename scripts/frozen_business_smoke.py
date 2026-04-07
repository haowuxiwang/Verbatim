from __future__ import annotations

import argparse
import json
import pickle
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.sample_assets import resolve_sample_path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run a business smoke path through the background task worker")
    p.add_argument("--exe", default="", help="Packaged Verbatim executable path")
    p.add_argument("--left-pdf", default="original.pdf")
    p.add_argument("--right-pdf", default="digest.pdf")
    p.add_argument("--left-page", type=int, default=0)
    p.add_argument("--top-k-pages", type=int, default=1)
    p.add_argument("--top-k-regions", type=int, default=1)
    p.add_argument("--min-score", type=float, default=0.05)
    return p.parse_args()


def _runner_command(exe_path: Path | None) -> list[str]:
    if exe_path is not None:
        return [str(exe_path), "--background-task-worker"]
    return [sys.executable, str(ROOT / "main.py"), "--background-task-worker"]


def main() -> int:
    args = parse_args()
    exe_arg = str(args.exe or "").strip()
    exe_path = Path(exe_arg).resolve() if exe_arg else None
    if exe_path is not None and not exe_path.exists():
        print(f"SMOKE_ERROR exe_missing:{exe_path}")
        return 2

    left_pdf = resolve_sample_path(args.left_pdf)
    right_pdf = resolve_sample_path(args.right_pdf)
    if not left_pdf.exists() or not right_pdf.exists():
        print(f"SMOKE_ERROR sample_missing left={left_pdf} right={right_pdf}")
        return 2

    payload = {
        "args": (str(left_pdf), str(right_pdf), int(args.left_page)),
        "kwargs": {
            "top_k_pages": int(args.top_k_pages),
            "min_score": float(args.min_score),
            "top_k_regions": int(args.top_k_regions),
        },
    }

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        input_path = tmp / "input.pkl"
        output_path = tmp / "output.pkl"
        input_path.write_bytes(pickle.dumps(payload))
        cmd = [
            *_runner_command(exe_path),
            "--task",
            "compute_prealign_payload",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ]
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode != 0:
            out = (proc.stdout or "").strip()
            print(f"SMOKE_ERROR worker_failed rc={proc.returncode}")
            if out:
                print(out)
            return 2
        if not output_path.exists():
            print("SMOKE_ERROR worker_output_missing")
            return 2
        result = pickle.loads(output_path.read_bytes())

    page_candidates = list(getattr(result, "page_candidates", []) or [])
    items_payload = list(getattr(result, "items_payload", []) or [])
    ok = bool(page_candidates and items_payload)
    summary = {
        "mode": "exe" if exe_path is not None else "source",
        "exe": str(exe_path) if exe_path is not None else "",
        "left_pdf": str(left_pdf),
        "right_pdf": str(right_pdf),
        "page_candidates": len(page_candidates),
        "items_payload": len(items_payload),
    }
    print("BUSINESS_SMOKE " + json.dumps(summary, ensure_ascii=False))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
