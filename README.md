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

Run tests:

```powershell
python -m pytest -q
```

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
- `app.main_window` is still excluded from strict mypy coverage.
- `python scripts/pyinstaller_release_gate.py --skip-exe-run` now includes a source-side business smoke, but it is still not a full frozen-app acceptance signal.
