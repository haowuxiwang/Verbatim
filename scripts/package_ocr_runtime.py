#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


def build_manifest(runtime_dir: Path) -> dict:
    files = list(runtime_dir.rglob("*"))
    file_count = 0
    total_size = 0
    for f in files:
        if f.is_file():
            file_count += 1
            total_size += f.stat().st_size
    return {
        "runtime_dir": str(runtime_dir.resolve()),
        "file_count": file_count,
        "size_mb": round(total_size / (1024 * 1024), 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Package OCR runtime directory into zip")
    ap.add_argument("--runtime-dir", required=True, help="OCR runtime directory")
    ap.add_argument("--out", default="dist/ocr_runtime.zip", help="output zip path")
    args = ap.parse_args()

    runtime_dir = Path(args.runtime_dir)
    if not runtime_dir.exists() or not runtime_dir.is_dir():
        raise FileNotFoundError(f"runtime dir not found: {runtime_dir}")

    out_zip = Path(args.out)
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp_base = out_zip.with_suffix("")
    if tmp_base.exists():
        shutil.rmtree(tmp_base, ignore_errors=True)
    tmp_base.mkdir(parents=True, exist_ok=True)

    manifest = build_manifest(runtime_dir)
    (tmp_base / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Put runtime as ocr_runtime/ in archive root.
    staging = tmp_base / "ocr_runtime"
    shutil.copytree(runtime_dir, staging, dirs_exist_ok=True)

    # make_archive returns output path without .zip suffix handling differences.
    archive_without_ext = str(out_zip.with_suffix(""))
    shutil.make_archive(archive_without_ext, "zip", root_dir=tmp_base)

    final_zip = out_zip.with_suffix(".zip")
    print(
        json.dumps(
            {
                "ok": True,
                "zip": str(final_zip.resolve()),
                "manifest": manifest,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
