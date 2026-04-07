# P3 CLI & Packaging Report (2026-03-05)

## Scope

- Validate OCR route fallback behavior from command line.
- Validate dual-package output (`Verbatim.exe` + `ocr_runtime.zip`).
- Re-run full test suite with coverage to assess industrialization gap.

## Command-line OCR route results

Input sample:
- pdf: `digest.pdf`
- page: `0`
- bbox: `50,120,500,220`
- repeat: `2`

### Route: `local_first`

- availability: `1.0` (2/2 success)
- fallback_count: `2` (local failed -> cloud succeeded)
- local failure reason: `local paddleocr import failed: [WinError 5] access denied to C:\\Users\\WuSiTan\\.paddlex`
- cloud quality snapshot: `warning`, confidence `78`

### Route: `cloud_only`

- availability: `1.0` (2/2 success)
- fallback_count: `0`
- quality snapshot: `warning`, confidence `78`

### Route: `local_only`

- availability: `0.0` (0/2 success)
- fail reason: same local permission issue as above
- trustworthiness: `false`

## Fallback strategy conclusion

- Current fallback chain is functioning as designed:
  - `local_first`: local fails, cloud takes over, flow remains available.
- Current blocker for fully-offline mode:
  - local runtime assets (font/model) are not staged yet.
- Hardening done:
  - strict offline mode now fails fast on missing local assets instead of attempting network download.
- Action:
  - stage local runtime assets and set `VERBATIM_OCR_RUNTIME_DIR`, then validate with `local_only`.

## Packaging output

- Main package:
  - `dist/Verbatim.exe`
  - size: `174.8 MB`
- OCR runtime package (sample):
  - `dist/ocr_runtime.zip`
  - built by `scripts/package_ocr_runtime.py`

## Test and coverage

Command:
- `pytest --cov=. --cov-report=term-missing -q`

Result:
- `157 passed`
- total coverage: `75%`

Key low-coverage modules:
- `app/main_window.py`: `61%`
- `core/services/ocr_engines.py`: `69%`
- `build.py`: `0%`
- `scripts/ocr_route_smoke_test.py`: `20%`

## Industrialization gap (current)

- Reliability:
  - main flow is stable with cloud fallback, but local-only is not production-ready yet.
- Explainability:
  - better now (CLI route diagnostics + manual review gate), still needs UI-level runtime-missing guidance.
- Engineering quality:
  - coverage is acceptable overall but insufficient in high-change surfaces (`main_window.py`, route logic, scripts/build path).

## Next actions

1. Add local runtime permission/self-check and user-facing remediation guidance.
2. Add first-load warmup hint for local model initialization.
3. Add focused tests for:
   - `local_only` failure classification branches,
   - runtime-missing UI guidance path,
   - packaging scripts and `build.py` smoke path.
