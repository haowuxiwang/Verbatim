# Archive

This directory holds repository history that is intentionally kept out of the active build and runtime entrypoints.

## Boundaries

- `specs/`: historical PyInstaller specs retained for forensic reference only
- active packaging entrypoint remains the repo-root `Verbatim.spec`

Nothing under `archive/` should be treated as a supported runtime or release input unless explicitly referenced by current build scripts.
