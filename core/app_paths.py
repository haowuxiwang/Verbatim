from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

APP_NAME = "Verbatim"
DATA_DIR_ENV_VAR = "VERBATIM_DATA_DIR"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def legacy_runtime_dir() -> Path:
    return repo_root() / "mappings"


def default_user_data_dir() -> Path:
    override = os.getenv(DATA_DIR_ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser()
    if sys.platform.startswith("win"):
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        if base:
            return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    xdg_home = os.getenv("XDG_DATA_HOME", "").strip()
    if xdg_home:
        return Path(xdg_home).expanduser() / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


def runtime_state_dir() -> Path:
    path = default_user_data_dir()
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        fallback = Path(tempfile.gettempdir()) / APP_NAME
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def runtime_state_path(filename: str) -> Path:
    path = runtime_state_dir() / filename
    _seed_from_legacy_repo_file(path, legacy_runtime_dir() / filename)
    return path


def _seed_from_legacy_repo_file(target: Path, legacy: Path) -> None:
    if target.exists() or not legacy.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(legacy, target)
