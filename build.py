#!/usr/bin/env python
"""
Build script for Verbatim PDF comparison tool.
Creates a standalone executable using PyInstaller.
"""

import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Add current directory to Python path
sys.path.insert(0, str(Path(__file__).parent))

CANONICAL_SPEC = Path("Verbatim.spec")
CANONICAL_DIST_DIR = Path("dist/Verbatim")
CANONICAL_EXE = CANONICAL_DIST_DIR / "Verbatim.exe"


def clean_build():
    """Clean previous build artifacts."""
    build_dirs = ["build", "dist", "__pycache__"]
    for dir_name in build_dirs:
        dir_path = Path(dir_name)
        if dir_path.exists():
            shutil.rmtree(dir_path)
            print(f"Cleaned {dir_name}")


def run_quality_gate():
    """Run pre-build quality gate unless explicitly building beta."""
    if os.getenv("VERBATIM_BETA_BUILD", "0").strip() in {"1", "true", "True"}:
        print("Quality gate skipped (beta build mode).")
        return

    cmd = [sys.executable, "-m", "pytest", "-q"]
    print("Running quality gate:", " ".join(cmd))
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"\nQuality gate failed with exit code: {result.returncode}")
        print("Set VERBATIM_BETA_BUILD=1 only for limited beta trials.")
        sys.exit(result.returncode)


def build_executable():
    """Build the executable using the canonical PyInstaller spec."""
    print("Building Verbatim executable...")
    if not CANONICAL_SPEC.exists():
        print(f"Error: canonical spec not found: {CANONICAL_SPEC}")
        sys.exit(1)

    cmd = ["pyinstaller", "--noconfirm", "--clean", str(CANONICAL_SPEC)]

    print("Running:", " ".join(cmd))
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"\nBuild failed with exit code: {result.returncode}")
        sys.exit(result.returncode)

    print("\nBuild completed!")


def check_output():
    """Check the output executable."""
    exe_path = CANONICAL_EXE
    if exe_path.exists():
        size_mb = exe_path.stat().st_size / (1024 * 1024)
        print(f"\nExecutable created: {exe_path}")
        print(f"File size: {size_mb:.1f} MB")

        # Test if it runs (dry run)
        print("\nTesting executable...")
        probe = subprocess.run([str(exe_path), "--help"], check=False, capture_output=True, text=True)
        if probe.returncode == 0:
            print("Executable runs successfully!")
        else:
            print(f"Executable probe returned code {probe.returncode} (GUI mode may ignore --help)")
    else:
        print("❌ Executable not found!")
        sys.exit(1)


if __name__ == "__main__":
    print("=== Verbatim Build Script ===")
    print(f"Build time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Check if we're in the right directory
    if not Path("main.py").exists():
        print("Error: main.py not found. Please run this script from the project root.")
        sys.exit(1)

    # Check virtual environment
    if not Path(".venv").exists():
        print("Warning: Virtual environment not found. Dependencies might be missing.")

    run_quality_gate()
    clean_build()
    build_executable()
    check_output()

    print("\n=== Build Summary ===")
    print("The executable can be found in the 'dist' directory.")
    print(f"To distribute, copy '{CANONICAL_DIST_DIR}' to the target machine.")
