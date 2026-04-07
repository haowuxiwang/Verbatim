# Verbatim Industrialization TodoList

Updated: 2026-03-04 (runtime stability patch)
Scope: local dev version to industrial-grade `.exe` distribution baseline

## P0 Must Have (release blocking)

- [x] Fix `auto_match` type mismatch crash risk
  - Goal: `suggest_mappings()` must use `RegionData` candidates, not raw `CharData`
  - Acceptance: unit test covers basic call path without exception
  - Status: completed

- [x] Disable sensitive plain-text logging by default
  - Goal: do not print full selected/OCR text and full diff JSON in default runs
  - Acceptance: logs show only length/status; explicit env toggle for debug text
  - Status: completed

- [x] Secure OCR token storage
  - Goal: Windows stores token with DPAPI, not plaintext
  - Acceptance: config file contains `token_enc` on Windows; legacy plaintext config auto-migrates
  - Status: completed

- [x] Fix oversized/min-width constrained GUI on 1920-class displays
  - Goal: remove frequent `QWindowsWindow::setGeometry` minimum-track over-constraint warnings and improve adaptability across monitor sizes
  - Acceptance: top bar split into two rows, viewer min-size pressure reduced, app remains usable at 1920 width
  - Status: completed (code-level; pending your real-machine visual confirmation)

- [x] Keep right result panel visible (non-collapsible)
  - Goal: avoid accidental collapse making diff list/details unreadable
  - Acceptance: splitter children non-collapsible + right panel minimum width enforced
  - Status: completed

- [x] Clean top-bar UX and move secondary toggles to advanced settings
  - Goal: reduce visual noise and keep primary workflow focused
  - Acceptance: `显示格式差异` / `忽略空白` removed from top bar and configurable in `高级设置`
  - Status: completed

- [x] Polish button visual style
  - Goal: remove heavy outlined/button-line look in main toolbar
  - Acceptance: primary toolbar buttons use flat fill style with lighter hover states
  - Status: completed

## P1 High Priority (stability/maintainability)

- [x] Atomic persistence for region selections
  - Goal: prevent corruption of `region_selections.json` on crashes
  - Acceptance: `tmp + replace`, fsync, corrupted file isolation on load failure
  - Status: completed

- [x] Build script reliability
  - Goal: correct exit-code handling for build/probe
  - Acceptance: `subprocess.run` based execution and return-code checks
  - Status: completed

- [x] Replace UI event-pump polling with Qt worker thread model
  - Goal: remove `while is_alive + processEvents` loop in OCR path
  - Acceptance: switched to `QThread + QEventLoop` runner; no manual pump loop
  - Status: completed (next iteration can move further to `QThreadPool/QFuture` + cancellation)

- [x] Fix `QThread: Destroyed while thread is still running` crash risk in OCR timeout path
  - Goal: timeout should not crash on app close or subsequent interactions
  - Acceptance: background OCR wrapper no longer depends on `QThread` lifecycle; timeout raises safely
  - Status: completed (migrated to daemon-thread wrapper + UI event pump)

- [x] Shrink OCR retry chain and enforce remaining-time budget
  - Goal: avoid 30s+ user wait caused by sync timeout + async retry + expanded retry chain
  - Acceptance: each cloud call uses remaining budget timeout; sync->async retry default disabled; expanded retry skipped on cloud `空文件`
  - Status: completed

- [x] Improve local OCR failure observability
  - Goal: quickly diagnose `process_error` root cause
  - Acceptance: logs include local tesseract return code, reason classification, stderr snippet
  - Status: completed

- [x] Add low-reliability guard for OCR diff output
  - Goal: prevent false-positive diffs when OCR confidence is unstable but texts are highly similar
  - Acceptance: in OCR mode, high-similarity + low-confidence micro-diffs are suppressed and summary downgraded to manual review
  - Status: completed

- [x] Apply low-confidence noise suppression in OCR path
  - Goal: reduce tiny noisy content diffs in OCR-assisted comparisons
  - Acceptance: low-confidence noise filter now applies to OCR and non-OCR paths
  - Status: completed

- [x] Disable field mapping by default in OCR/low-quality scenarios
  - Goal: avoid field-level amplified false alarms under weak OCR evidence
  - Acceptance: field mapping auto-disabled when OCR is used or text quality is non-good
  - Status: completed

- [x] Add compare decision gate (`PASS/REVIEW`) before publishing OCR diffs
  - Goal: avoid producing hard diff conclusions from unstable OCR evidence
  - Acceptance: OCR timeout/low-confidence/high-similarity scenarios downgrade to `REVIEW` and suppress diff output
  - Status: completed

- [x] Add local OCR stability double-check (same bbox second pass)
  - Goal: reduce random local OCR acceptance that causes run-to-run diff drift
  - Acceptance: unstable second-pass local OCR falls back to cloud path (or review downgrade)
  - Status: completed

- [x] Switch OCR routing to cloud-priority (Paddle API first, local optional fallback)
  - Goal: recover previously acceptable noise profile while keeping local engine as controlled fallback
  - Acceptance: with cloud config, route uses Paddle first by default; local can be re-enabled via env switch
  - Status: completed

- [x] Remove local OCR runtime path and artifacts (cloud-only OCR)
  - Goal: reduce noise and package bloat from low-quality local fallback
  - Acceptance: compare pipeline no longer executes local OCR branch; `ocr/` directory removed; tests pass
  - Status: completed

- [x] Wire compare history into GUI with replay
  - Goal: avoid "latest compare overwrites previous evidence" by allowing history recall
  - Acceptance: right panel shows compare history list; click item restores pages/selection and replays saved diff list/overlays when available
  - Status: completed

- [x] Split `main.py` by layers
  - Goal: separate UI, app service, and domain logic
  - Acceptance: `main.py` < 1200 lines and logic modules covered by tests
  - Status: completed (`main.py` reduced to launcher entrypoint; UI moved to `app/main_window.py`; orchestration logic split into service modules and covered by tests)

## P2 Mid-term Engineering

- [x] Structured logging + error code taxonomy
  - Goal: unified logger, redaction policy, traceable error codes
  - Acceptance: key paths log with machine-parseable fields and no sensitive plaintext
  - Status: completed (`core/services/observability.py` in use; compare/OCR key events instrumented; `docs/error_codes.md` added)

- [x] Dependency reduction and packaging minimization
  - Goal: remove non-runtime heavy dependencies from lock/build path
  - Acceptance: lean lockfile and smaller install/build footprint
  - Status: completed (split into `requirements-runtime.txt`, `requirements-build.txt`, `requirements-dev.txt`; runtime set minimized)

- [x] Integration and packaging validation tests
  - Goal: cover GUI compare flow, OCR fallback failure paths, config migration, packaged executable sanity
  - Acceptance: CI pipeline includes integration matrix and passes consistently
  - Status: completed (added non-GUI pipeline integration baseline + OCR/config migration and orchestrator tests; packaging probe retained in build script)

