from __future__ import annotations

import argparse
import pickle
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from core.services.background_tasks import (
    assess_pdf_side_quality,
    build_document_profile_task,
    compute_prealign_payload,
    compute_visual_diff,
    load_page_bundle,
    parse_page_task,
    render_page_png,
    render_region_with_meta,
    sleep_task,
)

TASKS: dict[str, Callable[..., Any]] = {
    "assess_pdf_side_quality": assess_pdf_side_quality,
    "build_document_profile": build_document_profile_task,
    "compute_visual_diff": compute_visual_diff,
    "compute_prealign_payload": compute_prealign_payload,
    "load_page_bundle": load_page_bundle,
    "parse_page": parse_page_task,
    "render_page_png": render_page_png,
    "render_region_with_meta": render_region_with_meta,
    "sleep": sleep_task,
}


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Background task worker")
    p.add_argument("--task", required=True)
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    task = TASKS.get(str(args.task))
    if task is None:
        raise ValueError(f"unknown task: {args.task}")

    input_path = Path(str(args.input))
    output_path = Path(str(args.output))
    payload = pickle.loads(input_path.read_bytes())
    call_args = tuple(payload.get("args", ()))
    call_kwargs = dict(payload.get("kwargs", {}))
    try:
        result = task(*call_args, **call_kwargs)
        output_path.write_bytes(pickle.dumps(result))
        return 0
    except Exception:
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv[1:]))
