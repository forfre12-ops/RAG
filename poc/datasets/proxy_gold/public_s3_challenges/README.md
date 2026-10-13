# Public S3 challenge registry

These artifacts are all-S3 public-document overclassification challenges. They
must stay separate from the balanced synthetic proxy evaluation and cannot
support customer-real or overall-accuracy claims.

## Development diagnostic

- `public-s3-300-20260808-v1`
- records SHA-256:
  `a03885ef776db07648bf53a91684f63b5009f0880998136ddfeb2d6243fe4ed4`
- Status: opened and evaluated against deployed model `v-fe4b386b`.
- Allowed use: development diagnosis and paired regression during development.
- It is no longer a blind final holdout. Its observed FPR was 31.0%, so using
  that result to alter training or thresholds creates benchmark feedback.

## Sealed final holdout

- `public-s3-300-blind-20260808-v2`
- records SHA-256:
  `dd4f94e1533fea607c72455f1d1861cc2d0667a5ebb1853df8846f03ee49b802`
- Status: assembled and hash-sealed; no model predictions have been run.
- It was selected from non-overlapping listing pages 46-90 while blocking all
  v1 document IDs, document-family IDs, and normalized text hashes.
- Allowed use: one final public-real S3 evaluation only after the candidate
  model, calibration, thresholds, and release criteria are frozen.
- Do not inspect per-document predictions or use this artifact for training,
  tuning, hard-negative mining, checkpoint selection, or calibration.

