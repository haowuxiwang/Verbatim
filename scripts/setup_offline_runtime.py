#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import shutil
import ssl
import tarfile
import time
import urllib.request
from pathlib import Path

DET_URL = "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv5_mobile_det_infer.tar"
REC_URL = "https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv5_mobile_rec_infer.tar"


def ensure_dirs(runtime_dir: Path) -> None:
    (runtime_dir / "assets" / "fonts").mkdir(parents=True, exist_ok=True)
    (runtime_dir / "models" / "PP-OCRv5_mobile_det").mkdir(parents=True, exist_ok=True)
    (runtime_dir / "models" / "PP-OCRv5_mobile_rec").mkdir(parents=True, exist_ok=True)
    (runtime_dir / ".paddlex" / "fonts").mkdir(parents=True, exist_ok=True)


def copy_font(runtime_dir: Path) -> tuple[bool, str]:
    dst = runtime_dir / "assets" / "fonts" / "simfang.ttf"
    if dst.exists():
        return True, str(dst)
    candidates = [
        Path(r"C:\Windows\Fonts\simfang.ttf"),
        Path(r"C:\Windows\Fonts\simsun.ttc"),
        Path(r"C:\Windows\Fonts\msyh.ttc"),
    ]
    for c in candidates:
        if c.exists():
            shutil.copy2(c, dst)
            return True, str(dst)
    return False, "no system CJK font found under C:\\Windows\\Fonts"


def download(url: str, out_file: Path, retries: int = 3, timeout_sec: int = 45) -> tuple[bool, str]:
    ctx = ssl._create_unverified_context()  # noqa: SLF001
    last_err = ""
    for i in range(retries):
        try:
            req = urllib.request.Request(
                url=url,
                headers={"User-Agent": "Mozilla/5.0 VerbatimOfflineSetup"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=timeout_sec, context=ctx) as resp:
                data = resp.read()
            out_file.write_bytes(data)
            return True, f"downloaded {len(data)} bytes"
        except Exception as e:
            last_err = str(e)
            if i + 1 < retries:
                time.sleep(1.5 * (2**i))
    return False, last_err


def extract_model_tar(tar_path: Path, model_dir: Path) -> tuple[bool, str]:
    if not tar_path.exists():
        return False, f"tar not found: {tar_path}"
    try:
        with tarfile.open(tar_path, "r:*") as tf:
            members = tf.getmembers()
            # Strip first path component to match expected runtime layout.
            for m in members:
                parts = Path(m.name).parts
                if len(parts) <= 1:
                    continue
                m.name = str(Path(*parts[1:]))  # type: ignore[attr-defined]
                tf.extract(m, path=model_dir)
        return True, "ok"
    except Exception as e:
        return False, str(e)


def has_inference_yml(model_dir: Path) -> bool:
    return (model_dir / "inference.yml").exists()


def main() -> int:
    ap = argparse.ArgumentParser(description="Setup offline OCR runtime for Verbatim")
    ap.add_argument("--runtime-dir", default="ocr_runtime")
    ap.add_argument("--skip-download", action="store_true")
    ap.add_argument("--det-tar", default="", help="local det model tar path")
    ap.add_argument("--rec-tar", default="", help="local rec model tar path")
    args = ap.parse_args()

    runtime_dir = Path(args.runtime_dir).resolve()
    ensure_dirs(runtime_dir)

    report: dict = {
        "runtime_dir": str(runtime_dir),
        "font": {},
        "det": {},
        "rec": {},
        "ready": False,
    }

    ok_font, font_msg = copy_font(runtime_dir)
    report["font"] = {"ok": ok_font, "message": font_msg}

    det_dir = runtime_dir / "models" / "PP-OCRv5_mobile_det"
    rec_dir = runtime_dir / "models" / "PP-OCRv5_mobile_rec"
    det_tar = runtime_dir / "PP-OCRv5_mobile_det_infer.tar"
    rec_tar = runtime_dir / "PP-OCRv5_mobile_rec_infer.tar"
    if args.det_tar.strip():
        det_tar = Path(args.det_tar).resolve()
    if args.rec_tar.strip():
        rec_tar = Path(args.rec_tar).resolve()

    if not args.skip_download:
        if not det_tar.exists():
            det_ok, det_msg = download(DET_URL, det_tar)
            report["det"]["download"] = {"ok": det_ok, "message": det_msg, "url": DET_URL}
        else:
            report["det"]["download"] = {"ok": True, "message": f"using local tar: {det_tar}", "url": DET_URL}
        if not rec_tar.exists():
            rec_ok, rec_msg = download(REC_URL, rec_tar)
            report["rec"]["download"] = {"ok": rec_ok, "message": rec_msg, "url": REC_URL}
        else:
            report["rec"]["download"] = {"ok": True, "message": f"using local tar: {rec_tar}", "url": REC_URL}

    if det_tar.exists() and not has_inference_yml(det_dir):
        ex_ok, ex_msg = extract_model_tar(det_tar, det_dir)
        report["det"]["extract"] = {"ok": ex_ok, "message": ex_msg}
    if rec_tar.exists() and not has_inference_yml(rec_dir):
        ex_ok, ex_msg = extract_model_tar(rec_tar, rec_dir)
        report["rec"]["extract"] = {"ok": ex_ok, "message": ex_msg}

    report["det"]["inference_yml"] = str(det_dir / "inference.yml")
    report["rec"]["inference_yml"] = str(rec_dir / "inference.yml")
    report["det"]["ready"] = has_inference_yml(det_dir)
    report["rec"]["ready"] = has_inference_yml(rec_dir)
    report["ready"] = bool(ok_font and report["det"]["ready"] and report["rec"]["ready"])

    report["next_steps"] = [
        f"set VERBATIM_OCR_RUNTIME_DIR={runtime_dir}",
        "set VERBATIM_OCR_ROUTE=local_only",
        "set VERBATIM_OCR_OFFLINE_STRICT=1",
        'python scripts/ocr_route_smoke_test.py --pdf digest.pdf --page 0 --bbox "50,120,500,220" --repeat 2 --route local_only',
    ]
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
