from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

CANONICAL_SPEC = "Verbatim.spec"
SOURCE_SMOKE_TESTS = [
    "tests/test_compare_history.py",
    "tests/test_ocr_engines.py",
    "tests/test_zoom_gui.py",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PyInstaller release gate")
    p.add_argument("--exe", default="")
    p.add_argument("--startup-timeout-sec", type=int, default=12)
    p.add_argument("--skip-exe-run", action="store_true")
    p.add_argument("--skip-source-tests", action="store_true")
    return p.parse_args()


def _check_required_files(root: Path) -> dict[str, bool]:
    req = {
        CANONICAL_SPEC: root / CANONICAL_SPEC,
        "main.py": root / "main.py",
        "build.py": root / "build.py",
        "ocr_runtime/models/det": root / "ocr_runtime" / "models" / "PP-OCRv5_mobile_det",
        "ocr_runtime/models/rec": root / "ocr_runtime" / "models" / "PP-OCRv5_mobile_rec",
        "ocr_runtime/font": root / "ocr_runtime" / "assets" / "fonts" / "simfang.ttf",
    }
    return {k: v.exists() for k, v in req.items()}


def _check_canonical_build_entry(root: Path) -> tuple[bool, str]:
    build_script = root / "build.py"
    if not build_script.exists():
        return False, "build_py_missing"
    content = build_script.read_text(encoding="utf-8", errors="replace")
    if CANONICAL_SPEC in content and "pyinstaller" in content:
        return True, "build_uses_canonical_spec"
    return False, "build_not_using_canonical_spec"


def _run_source_smoke_tests(root: Path) -> tuple[bool, str]:
    cmd = ["python", "-m", "pytest", "-q", *SOURCE_SMOKE_TESTS]
    proc = subprocess.run(
        cmd,
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    output = (proc.stdout or "").strip().splitlines()
    tail = "\n".join(output[-8:]) if output else ""
    return proc.returncode == 0, tail


def _smoke_run_exe(exe_path: Path, timeout_sec: int) -> tuple[bool, str]:
    if not exe_path.exists():
        return False, "exe_missing"
    ps = (
        "$ErrorActionPreference='SilentlyContinue'; "
        f"$p=Start-Process -FilePath '{str(exe_path)}' -PassThru; "
        f"Start-Sleep -Seconds {max(1, int(timeout_sec))}; "
        "if ($p.HasExited) { Write-Output ('EXITED:' + $p.ExitCode) } "
        "else { Write-Output 'RUNNING'; Stop-Process -Id $p.Id -Force }"
    )
    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps],
            text=True,
            encoding="utf-8",
            errors="replace",
        ).strip()
        if "RUNNING" in out:
            return True, "launch_ok_running"
        if "EXITED:" in out:
            return False, f"early_exit:{out}"
        return False, f"unknown_smoke_output:{out}"
    except Exception as e:
        return False, f"launch_error:{e}"


def main() -> int:
    args = parse_args()
    root = Path.cwd()
    checks = _check_required_files(root)
    req_ok = all(checks.values())
    build_entry_ok, build_entry_note = _check_canonical_build_entry(root)

    if str(args.exe or "").strip():
        exe_path = (root / args.exe).resolve()
    else:
        onedir = (root / "dist" / "Verbatim" / "Verbatim.exe").resolve()
        onefile = (root / "dist" / "Verbatim.exe").resolve()
        exe_path = onedir if onedir.exists() else onefile
    smoke_ok = True
    smoke_note = "skipped"
    if not args.skip_exe_run:
        smoke_ok, smoke_note = _smoke_run_exe(exe_path, int(args.startup_timeout_sec))
    source_ok = True
    source_note = "skipped"
    if not args.skip_source_tests:
        source_ok, source_note = _run_source_smoke_tests(root)

    result = {
        "required_checks": checks,
        "required_ok": req_ok,
        "build_entry_ok": build_entry_ok,
        "build_entry_note": build_entry_note,
        "exe_path": str(exe_path),
        "source_smoke_ok": source_ok,
        "source_smoke_note": source_note,
        "smoke_ok": smoke_ok,
        "smoke_note": smoke_note,
        "gate_pass": bool(req_ok and build_entry_ok and source_ok and smoke_ok),
    }
    print("RELEASE_GATE " + json.dumps(result, ensure_ascii=False))
    return 0 if result.get("gate_pass") else 2


if __name__ == "__main__":
    raise SystemExit(main())
