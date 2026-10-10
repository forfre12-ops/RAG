# Release Checklist

Current release status: `CONDITIONALLY_READY` (strict release gate = **FAIL**).

**Current hard blocker:**
- `human_review` gold is **1/40**. Add 39 real reviewer-signed rows before strict release.
- P1 release-tier gate now passes: public/case/nkt F1 **0.938 >= 0.75**, FNR **0.043 <= 0.05**, high-risk->S3 **0**.
- Strict `release-gate` still fails until `human_review` reaches the configured minimum and passes agreement checks.
> Do not fake-green this with `ai_assist`, `llm_*`, blank, or placeholder reviewer IDs; the importer must reject them.

## 0. 릴리스 재현성 및 모델 일치 사전점검

**(a) 깨끗한 작업 트리·태그·매니페스트에서만 릴리스합니다.** 변경이 남은 작업 트리에서는 배포하지 않습니다.
```bash
git status --porcelain     # 출력이 비어 있지 않으면 커밋 또는 스태시 후 다시 확인
git tag -a vX.Y.Z -m "..." # 릴리스는 태그가 가리키는 커밋에서만 빌드
make release-manifest      # 산출물·소스 버전을 reports/release_manifest.json에 고정
```

**(b) 배포 모델과 평가 모델을 일치시킵니다(parity gate).** 릴리스 후보 `v-dd3abab9`,
`CLASSIFIER_MODEL_DIR`, `P1_MODEL`, readiness 기본값은 모두 같은 모델을 가리켜야 합니다.

1. 배포 후보 모델을 하나로 확정합니다(현재 기본값: `v-dd3abab9`).
2. 그 모델로 P1 평가를 다시 만듭니다. `reports/p1_release_legal_direct.json`과 `reports/p1_release_holdout_direct.json`의 `model_dir`는 `CLASSIFIER_MODEL_DIR`와 같아야 합니다. 평가 파일만 바꿔 수치를 맞추지 않습니다.
3. `make operational-readiness`의 parity gate가 PASS인지 확인합니다. F1/FNR 보고서가 실제 배포 모델의 결과를 가리켜야 합니다.
> Parity is an internal deploy action: set `CLASSIFIER_MODEL_DIR` to the evaluated model and regenerate readiness.
> After parity passes, the remaining external blocker is `human_review=40/40`.

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

`reviewer_id=ai_assist`, `llm_*`, blank, or placeholder values must fail import.
That failure is intentional: only real person sign-off counts as `human_review`.

## 2. Rebuild Gates

```bash
make check-manifest
make p1-boundary
make operational-readiness
make release-gate
make release-manifest
```

Pre-human state: `make operational-readiness` should report `CONDITIONALLY_READY` with only
`human_review gold` blocked. `make release-gate` must remain blocked until the real
reviewer-signed queue reaches `human_review=40/40`.

## 3. P1 Promotion

Release candidate:

```bash
CLASSIFIER_MODEL_DIR=artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9
```

Rollback by restoring the previous `CLASSIFIER_MODEL_DIR` and restarting the API
workers. Keep the previous model directory mounted until the release is accepted.

## 4. P2 Retrieval Gate

Operational retrieval config:

```bash
EMBEDDING_MODEL=nlpai-lab/KURE-v1
VECTOR_BACKEND=pg
RAG_OPERATIONAL_SEARCH_MODE=hybrid
RAG_INDEX_CHUNK_SIZE=1200
RAG_INDEX_CHUNK_OVERLAP=100
```

Run after pgvector reindex, embedding-model change, or release candidate build:

```bash
make p2-full-gold
make p1-boundary
make operational-readiness
make release-gate
make release-manifest
```

Before strict production release, `make release-gate` must print `PASS`.

## 5. Final Test Pass

```bash
make test-lite
make test-fullstack
```

Use the `workflow_dispatch` release gate only when the required reports and model
artifacts are available to the runner.
