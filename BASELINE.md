# Baseline Candidate

This file defines the current repository baseline candidate before the first stable commit.

## Keep In Root

- `main.py`
- `build.py`
- `Verbatim.spec`
- `README.md`
- active dependency manifests
- active product or requirement documents that still drive implementation

## Keep As Source

- `app/`
- `core/`
- `scripts/`
- `tests/`
- `docs/`
- `ocr_runtime/`

## Keep As Archived History

- `archive/reviews/`
- `archive/specs/`

## Keep As Sample Assets

- `samples/manual-verification/`

## Keep As Local-Only Artifacts

- `local-artifacts/runtime-seeds/`
- `local-artifacts/runtime-state/`

## Environment Variable Conventions

| Variable | Purpose | Used by |
|---|---|---|
| `VERBATIM_OCR_WORKER_PYTHON` | Path to isolated OCR worker Python 3.11 interpreter | `LocalPaddleEngine` subprocess |
| `VERBATIM_BG_WORKER_PYTHON` | Path to general background task worker interpreter | `background_worker.py` subprocess |
| `VERBATIM_OCR_RUNTIME_DIR` | Path to OCR runtime assets (models, fonts) | `resolve_ocr_runtime_dir()` |
| `VERBATIM_PADDLEOCR_JSON_EXE` | Path to PaddleOCR-json CLI executable | `LocalPaddleOcrJsonEngine` |
| `VERBATIM_OCR_ROUTE` | OCR routing strategy (e.g. `local_first`) | `compare_orchestrator.py` |

`VERBATIM_OCR_WORKER_PYTHON` is only for the isolated OCR worker. General background tasks (rendering, parsing, prealign) use `VERBATIM_BG_WORKER_PYTHON` or fall back to the main app interpreter. These two must not point to the same venv unless that venv has all runtime dependencies installed.

## Must Not Return To Root

- runtime JSON such as compare history or region selections
- historical PyInstaller specs other than `Verbatim.spec`
- manual verification PDFs
- downloaded OCR tarballs
- one-off review logs and dated todo notes
