param(
  [switch]$Fix
)

$ErrorActionPreference = "Stop"

if ($Fix) {
  python -m ruff format .
  python -m ruff check . --fix
} else {
  python -m ruff format --check .
  python -m ruff check .
}

python -m mypy
