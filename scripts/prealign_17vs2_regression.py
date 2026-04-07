from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.sample_assets import resolve_sample_path
from core.services.prealign import build_document_profile, retrieve_page_candidates


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Prealign regression: original.pdf vs digest.pdf")
    p.add_argument("--left-pdf", default="original.pdf")
    p.add_argument("--right-pdf", default="digest.pdf")
    p.add_argument("--top-k", type=int, default=3)
    p.add_argument("--min-score", type=float, default=0.05)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    left_pdf = resolve_sample_path(args.left_pdf)
    right_pdf = resolve_sample_path(args.right_pdf)
    if not left_pdf.exists() or not right_pdf.exists():
        print("ERROR missing input PDFs")
        return 2

    left_doc = build_document_profile(left_pdf)
    right_doc = build_document_profile(right_pdf)
    candidates_map = retrieve_page_candidates(
        left_doc, right_doc, top_k=int(args.top_k), min_score=float(args.min_score)
    )

    out: dict[str, list[dict[str, object]]] = {}
    for left_page, candidates in candidates_map.items():
        out[str(left_page + 1)] = [
            {
                "right_page": int(c.right_page + 1),
                "score": round(float(c.score), 4),
                "text_sim": round(float(c.text_sim), 4),
                "anchor_sim": round(float(c.anchor_sim), 4),
                "failure_type": c.failure_type,
            }
            for c in candidates
        ]

    print("PREALIGN_CANDIDATES_JSON " + json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
