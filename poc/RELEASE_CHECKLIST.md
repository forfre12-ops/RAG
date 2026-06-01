# Release Checklist

Current release status: `CONDITIONALLY_READY`.

Strict release is blocked until at least 40 externally reviewed `human_review`
classification gold records are imported.

## 1. Build Human Review Queue

```bash
make human-review-queue
```

Send `datasets/corrections/human_review_queue.csv` to reviewers. They must fill:

- `review_decision`: `correct`, `corrected`, `rejected`, or `uncertain`
- `reason_code`
- `reviewer_id`

Import reviewed rows:

```bash
python scripts/import_review_corrections.py datasets/corrections/human_review_queue.csv --merge-gold --dry-run
python scripts/import_review_corrections.py datasets/corrections/human_review_queue.csv --merge-gold
```

## 2. Rebuild Gates

```bash
make check-manifest
make p1-boundary
make operational-readiness
make release-gate
make release-manifest
```

`make release-gate` must print `PASS`.

## 3. P1 Promotion

Release candidate:

```bash
CLASSIFIER_MODEL_DIR=artifacts/classifier_p1_retrain_v3/v-3443785f
```

Rollback by restoring the previous `CLASSIFIER_MODEL_DIR` and restarting the API
workers. Keep the previous model directory mounted until the release is accepted.

## 4. P2 Retrieval Gate

Operational retrieval config:

```bash
EMBEDDING_MODEL=nlpai-lab/KURE-v1
VECTOR_BACKEND=es
RAG_OPERATIONAL_SEARCH_MODE=hybrid
RAG_INDEX_CHUNK_SIZE=1200
RAG_INDEX_CHUNK_OVERLAP=100
```

Run after ES reindex, embedding-model change, or release candidate build:

```bash
make p2-full-gold
make p1-boundary
make operational-readiness
make release-gate
make release-manifest
```

## 5. Final Test Pass

```bash
make test-lite
make test-fullstack
```

Use the `workflow_dispatch` release gate only when the required reports and model
artifacts are available to the runner.
