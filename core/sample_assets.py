from __future__ import annotations

from pathlib import Path

from .app_paths import repo_root

SAMPLE_DIR = repo_root() / "samples" / "manual-verification"


def resolve_sample_path(path_like: str | Path) -> Path:
    path = Path(path_like)
    if path.exists():
        return path.resolve()
    if path.is_absolute():
        return path
    candidate = SAMPLE_DIR / path
    if candidate.exists():
        return candidate.resolve()
    return path.resolve()
