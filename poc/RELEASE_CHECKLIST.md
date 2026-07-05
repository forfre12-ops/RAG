# Release Checklist

Current release status: `CONDITIONALLY_READY` (strict release gate = **FAIL**).

**상용 릴리스 하드 블로커 (코드로 못 넘음 — 데이터 천장):**
- `human_review` 골든 **0/40** — 외부 검수 라벨 필요(§1). 이게 없으면 release-gate 영구 FAIL.
- P1 F1 **0.635 < 0.75**(정직 홀드아웃) — 합성-only + 실 S1/S2 데이터 부족의 구조적 한계.
  임계·비용은 이미 소진. **모델 스왑/재튜닝으로 못 넘음** — 실데이터가 유일 레버(9월).
> 억지로 통과시키지 말 것 = fake-green. release-gate FAIL 은 이 두 개를 정직하게 말하는 것이다.

## 0. 릴리스 재현성 & 모델 parity (pre-flight, 매 릴리스)

**(a) Clean-tree + tag + manifest 로만 릴리스한다.** dirty 작업트리에서 배포 금지(현재 ~60개 변경).
```bash
git status --porcelain     # 비어 있어야 함(아니면 커밋/스태시)
git tag -a vX.Y.Z -m "..." # 릴리스는 태그에서만 빌드
make release-manifest      # 산출물 해시 고정(reports/release_manifest.json)
```

**(b) 배포 모델 == 평가 모델 (parity 게이트).** 현재 리포트는 `v-f9b5cedb`(step3)를 평가했으나
릴리스 후보(체크리스트 §3·.env·리허설)는 `v-dd3abab9` → **불일치**(문서화된 드리프트). 릴리스 전:
1. 릴리스 후보 모델을 **하나로 확정**(권장 v-dd3abab9 — 배포/구성이 일관 지시).
2. 그 모델로 P1 eval **재생성**(reports/p1_step3_legal_direct.json·p1_step3_holdout_direct.json 의
   `model_dir` == `CLASSIFIER_MODEL_DIR`). eval 파이프라인으로만 재생성(수기 편집 금지).
3. `make operational-readiness` → parity 게이트가 PASS 인지 확인(F1/FNR 리포트가 라이브 모델을 기술).
> parity 는 CLASSIFIER_MODEL_DIR 설정+재평가로 자가해소되는 내부 액션이지, 데이터 블로커가 아니다.
> 단, parity 해소해도 P1·human_review 는 여전히 FAIL(위 하드 블로커).

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

## 5. Final Test Pass

```bash
make test-lite
make test-fullstack
```

Use the `workflow_dispatch` release gate only when the required reports and model
artifacts are available to the runner.
