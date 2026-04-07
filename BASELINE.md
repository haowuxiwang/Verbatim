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

## Must Not Return To Root

- runtime JSON such as compare history or region selections
- historical PyInstaller specs other than `Verbatim.spec`
- manual verification PDFs
- downloaded OCR tarballs
- one-off review logs and dated todo notes
