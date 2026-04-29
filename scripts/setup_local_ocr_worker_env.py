#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import venv
from pathlib import Path


def _venv_python(venv_dir: Path) -> Path:
    if sys.platform.startswith("win"):
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _pip_env(*, index_url: str, extra_index_url: str, no_index: bool, disable_pip_config: bool) -> dict[str, str]:
    env = dict(os.environ)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INDEX"] = "1" if no_index else ""
    env["PIP_INDEX_URL"] = index_url
    env["PIP_EXTRA_INDEX_URL"] = extra_index_url
    if disable_pip_config:
        env["PIP_CONFIG_FILE"] = os.devnull
    return env


def _run(cmd: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Create an isolated Python runtime for local PaddleOCR")
    ap.add_argument("--venv-dir", default=".venv-ocr", help="path to the OCR-only virtualenv")
    ap.add_argument(
        "--requirements",
        default="requirements-ocr-py311.txt",
        help="requirements file to install into the isolated OCR env",
    )
    ap.add_argument("--runtime-dir", default="ocr_runtime", help="OCR runtime dir for models/fonts")
    ap.add_argument("--install", action="store_true", help="install pinned OCR dependencies into the venv")
    ap.add_argument("--wheel-dir", default="", help="optional offline wheel directory for pip --find-links")
    ap.add_argument("--index-url", default="https://pypi.org/simple", help="pip index url for online installs")
    ap.add_argument("--extra-index-url", default="", help="optional extra pip index url")
    ap.add_argument("--no-index", action="store_true", help="force offline pip install mode")
    ap.add_argument(
        "--disable-pip-config",
        action="store_true",
        help="ignore global/user pip config such as custom mirrors or no-index settings",
    )
    args = ap.parse_args()

    repo_root = Path.cwd()
    venv_dir = Path(args.venv_dir).resolve()
    requirements = Path(args.requirements).resolve()
    runtime_dir = Path(args.runtime_dir).resolve()

    builder = venv.EnvBuilder(with_pip=True)
    builder.create(venv_dir)
    python_exe = _venv_python(venv_dir)

    report: dict[str, object] = {
        "venv_dir": str(venv_dir),
        "worker_python": str(python_exe),
        "requirements": str(requirements),
        "runtime_dir": str(runtime_dir),
        "created": python_exe.exists(),
        "install_requested": bool(args.install),
        "pip": {
            "index_url": str(args.index_url),
            "extra_index_url": str(args.extra_index_url),
            "no_index": bool(args.no_index),
            "disable_pip_config": bool(args.disable_pip_config),
        },
    }

    steps: list[dict[str, object]] = []
    if args.install:
        if not requirements.exists():
            report["ready"] = False
            report["error"] = f"requirements file not found: {requirements}"
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2
        pip_env = _pip_env(
            index_url=str(args.index_url),
            extra_index_url=str(args.extra_index_url),
            no_index=bool(args.no_index),
            disable_pip_config=bool(args.disable_pip_config),
        )
        pip_cmd = [str(python_exe), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"]
        steps.append(
            {
                "name": "bootstrap_pip",
                "cmd": pip_cmd,
                "result": _run(pip_cmd, cwd=repo_root, env=pip_env).__dict__,
            }
        )
        install_cmd = [str(python_exe), "-m", "pip", "install", "-r", str(requirements)]
        wheel_dir = str(args.wheel_dir or "").strip()
        if wheel_dir:
            install_cmd.extend(["--find-links", wheel_dir])
        steps.append(
            {
                "name": "install_ocr_requirements",
                "cmd": install_cmd,
                "result": _run(install_cmd, cwd=repo_root, env=pip_env).__dict__,
            }
        )
    report["steps"] = steps
    report["next_steps"] = [
        f"set VERBATIM_OCR_WORKER_PYTHON={python_exe}",
        f"set VERBATIM_OCR_RUNTIME_DIR={runtime_dir}",
        "set VERBATIM_OCR_ROUTE=local_first",
        "python main.py --local-ocr-self-check",
    ]
    report["ready"] = bool(report["created"])
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
