# Offline Local OCR Enable Todo (No Admin)

## Current status
- [x] Local strict mode enabled (`VERBATIM_OCR_OFFLINE_STRICT=1`)
- [x] Local font prepared: `ocr_runtime/assets/fonts/simfang.ttf`
- [x] Local model folders created:
  - `ocr_runtime/models/PP-OCRv5_mobile_det`
  - `ocr_runtime/models/PP-OCRv5_mobile_rec`
- [ ] Required model files not yet present
  - Missing example: `inference.yml` in det model dir

## Next executable steps
- [ ] Put full `PP-OCRv5_mobile_det` files into `ocr_runtime/models/PP-OCRv5_mobile_det`
- [ ] Put full `PP-OCRv5_mobile_rec` files into `ocr_runtime/models/PP-OCRv5_mobile_rec`
- [ ] Re-run strict local smoke test and require `availability=1.0`
- [ ] Run `python main.py` with `local_only` and verify logs contain no `OCR(cloud:`

## Git Bash commands
```bash
mkdir -p ./ocr_runtime/assets/fonts
cp /c/Windows/Fonts/simfang.ttf ./ocr_runtime/assets/fonts/simfang.ttf

export VERBATIM_OCR_RUNTIME_DIR="$PWD/ocr_runtime"
export VERBATIM_OCR_ROUTE="local_only"
export VERBATIM_OCR_OFFLINE_STRICT="1"

python scripts/ocr_route_smoke_test.py --pdf digest.pdf --page 0 --bbox "50,120,500,220" --repeat 2 --route local_only
```

## Latest validation result
- local import passed with local font.
- current hard blocker:
  - `[Errno 2] No such file or directory: ...\\PP-OCRv5_mobile_det\\inference.yml`

## 12. One-Script Setup (Executed)

- [x] Added `scripts/setup_offline_runtime.py` (create dirs + copy font + try download + extract + validate)
- [x] Executed script locally; confirmed TLS handshake failure to bcebos in this network
- [x] Confirmed current blocker narrowed to missing model files (`inference.yml`)
- [ ] Manually download det/rec model tar files via browser and place under `ocr_runtime/`
- [ ] Re-run setup script with local tar paths, then run smoke test to reach availability=1.0

## 13. Final Verification (Executed)

- [x] Imported local model tar files into runtime
- [x] Fixed local engine cache/home isolation to avoid user-profile permission issues
- [x] Fixed local engine repeated-run instability (same-thread local predict)
- [x] `local_only` smoke test passed: repeat=3, success=3, availability=1.0, trustworthy=true
