#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description="Initialize offline OCR runtime skeleton")
    ap.add_argument("--runtime-dir", default="ocr_runtime", help="runtime dir path")
    args = ap.parse_args()

    root = Path(args.runtime_dir)
    (root / "models").mkdir(parents=True, exist_ok=True)
    (root / "assets" / "fonts").mkdir(parents=True, exist_ok=True)
    (root / ".paddlex" / "fonts").mkdir(parents=True, exist_ok=True)

    checklist = {
        "runtime_dir": str(root.resolve()),
        "required": [
            str((root / "assets" / "fonts" / "simfang.ttf").resolve()),
            str((root / "models" / "PP-OCRv5_mobile_det").resolve()),
            str((root / "models" / "PP-OCRv5_mobile_rec").resolve()),
        ],
        "next_steps": [
            "Put simfang.ttf into assets/fonts",
            "Put PP-OCRv5_mobile_det model files into models/PP-OCRv5_mobile_det",
            "Put PP-OCRv5_mobile_rec model files into models/PP-OCRv5_mobile_rec",
            "Set VERBATIM_OCR_RUNTIME_DIR to this directory",
            "Set VERBATIM_OCR_ROUTE=local_only for strict offline verification",
        ],
    }
    print(json.dumps(checklist, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
