# Verbatim Error/Event Codes

This file tracks stable machine-readable codes used in structured logs.

## Compare Flow

- `CMP_START`: compare workflow started
- `CMP_END`: compare workflow ended
- `CMP_QUALITY_WARN`: text-layer quality warnings detected
- `CMP_RESULT_OK`: compare completed with diff counts
- `CMP_RESULT_UNRELIABLE`: compare aborted due to bad text quality and failed OCR fallback

## OCR Flow

- `OCR_DECISION`: OCR decision generated for both sides
- `OCR_VARIANT_FAIL`: one OCR variant failed
- `OCR_FAIL`: OCR failed for a side
- `OCR_ERROR_CLASSIFIED`: OCR error classified into error_type buckets
- `OCR_FALLBACK_APPLIED`: OCR fallback text replaced at least one side
- `OCR_SKIPPED_NO_CONFIG`: OCR recommended but no config/token available
- `OCR_CANDIDATE_SCORE`: OCR candidate scored
- `OCR_BEST_ACCEPTED`: OCR best candidate accepted
- `OCR_BEST_REJECTED`: OCR best candidate rejected
- `OCR_RETRY_EXPANDED_BBOX`: OCR retry with expanded bbox
- `OCR_RETRY_SKIPPED`: OCR retry skipped (empty file/timeout)
- `OCR_LOCAL_BREAKER_OPEN`: local OCR breaker opened
- `OCR_LOCAL_BREAKER_SKIP`: local OCR skipped due to breaker

`OCR_ERROR_CLASSIFIED` error_type values:

- `timeout`
- `empty_result`
- `auth`
- `model`
- `engine`
- `worker`
- `runtime`
- `network`
- `unknown`

## Prealign Flow

- `PREALIGN_PAGE_CANDIDATES`: prealign page candidates emitted

## Reading Order

- `READING_ORDER_USED`: reading-order mode selected
- `READING_ORDER_AUTO_OVERRIDE`: reading-order auto override applied

## Diagnostics

- `DIAG_EXPORT`: diagnostics exported

