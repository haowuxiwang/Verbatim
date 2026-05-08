# Verbatim

Verbatim is a desktop PDF comparison tool built with PySide6. The current supported build entry is `build.py`, which packages the app with the canonical spec `Verbatim.spec`.

## Repo boundaries

Source code lives under `app/`, `core/`, `scripts/`, and `tests/`.

Local runtime state is not part of the repo baseline:
- compare history and region selections now default to the user data directory
- local OCR runtime cache stays under `ocr_runtime/.runtime_home/`
- large manual verification inputs and generated outputs should stay out of version control

Historical review notes and stage-by-stage cleanup records live under `archive/reviews/`.
Historical packaging specs live under `archive/specs/`.
Manual regression PDFs live under `samples/manual-verification/`.
Local-only large artifacts such as downloaded OCR tarballs belong under `local-artifacts/`.
Legacy runtime JSON captured from manual sessions belongs under `local-artifacts/runtime-state/`, not under the repo root.

This repo is expected to work in a non-admin local workspace such as `D:\...`.
If the default user data directory is not writable, runtime state falls back to the system temp directory.

## Development

Run the app:

```powershell
python main.py
```

Bootstrap an isolated local OCR worker runtime:

```powershell
python scripts/setup_local_ocr_worker_env.py --venv-dir .venv-ocr
python scripts/setup_local_ocr_worker_env.py --venv-dir .venv-ocr --install --disable-pip-config
set VERBATIM_OCR_WORKER_PYTHON=%CD%\\.venv-ocr\\Scripts\\python.exe
```

If your environment injects `PIP_NO_INDEX=1` or a broken mirror, prefer `--disable-pip-config` so the OCR worker install uses the explicit index arguments from the script.
The default `requirements-ocr-py311.txt` bootstrap expects Python 3.11. If your shell is running Python 3.12+ / 3.13+, switch to a 3.11 interpreter before creating the OCR venv.
`VERBATIM_OCR_WORKER_PYTHON` is only for the isolated OCR worker. General PDF parsing/rendering background tasks keep using the main app interpreter unless you explicitly set `VERBATIM_BG_WORKER_PYTHON`.

If local OCR stays blocked, inspect the self-check directly:

```powershell
python main.py --local-ocr-self-check
```

Run tests:

```powershell
python -m pytest -q
```

CI baseline: 251 passed, 1 skipped (as of 2026-05-08).

Run the typed subset currently enforced in CI/review:

```powershell
python -m mypy core app/view_models.py app/diff_presenter.py
```

Run the release gate:

```powershell
python scripts/pyinstaller_release_gate.py --skip-exe-run
```

Build the packaged app:

```powershell
python build.py
```

## Current constraints

- `app/main_window.py` is still oversized and only partially extracted.
- `python scripts/pyinstaller_release_gate.py --skip-exe-run` still skips frozen-app launch and local OCR validation; use the full gate after building an `.exe`.
- Local PaddleOCR must run in an isolated Python runtime; the main development Python may be incompatible with Paddle/OpenCV ABI requirements.
- The OCR worker venv (`requirements-ocr-py311.txt`) requires Python 3.11. The setup script (`setup_local_ocr_worker_env.py`) validates this automatically and will refuse to install if the current interpreter is not 3.11.
- Frozen mode (`--local-ocr-self-check`) uses `sys.executable --local-ocr-worker` internally; no separate `VERBATIM_OCR_WORKER_PYTHON` is needed for packaged builds.
- All subprocess entry points (`main.py`) set UTF-8 encoding at startup to prevent mojibake on Chinese Windows systems where the default locale is GBK/cp936.
