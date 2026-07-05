# Release Checklist

Current release status: `CONDITIONALLY_READY` (strict release gate = **FAIL**).

**Current hard blocker:**
- `human_review` gold is **1/40**. Add 39 real reviewer-signed rows before strict release.
- P1 release-tier gate now passes: public/case/nkt F1 **0.938 >= 0.75**, FNR **0.043 <= 0.05**, high-risk->S3 **0**.
- Strict `release-gate` still fails until `human_review` reaches the configured minimum and passes agreement checks.
> Do not fake-green this with `ai_assist`, `llm_*`, blank, or placeholder reviewer IDs; the importer must reject them.

## 0. 由대━???ы쁽??& 紐⑤뜽 parity (pre-flight, 留?由대━??

**(a) Clean-tree + tag + manifest 濡쒕쭔 由대━?ㅽ븳??** dirty ?묒뾽?몃━?먯꽌 諛고룷 湲덉?(?꾩옱 ~60媛?蹂寃?.
```bash
git status --porcelain     # 鍮꾩뼱 ?덉뼱?????꾨땲硫?而ㅻ컠/?ㅽ깭??
git tag -a vX.Y.Z -m "..." # 由대━?ㅻ뒗 ?쒓렇?먯꽌留?鍮뚮뱶
make release-manifest      # ?곗텧臾??댁떆 怨좎젙(reports/release_manifest.json)
```

**(b) 諛고룷 紐⑤뜽 == ?됯? 紐⑤뜽 (parity 寃뚯씠??.** 由대━???꾨낫??`v-dd3abab9`?대ŉ,
`CLASSIFIER_MODEL_DIR`, `P1_MODEL`, readiness 湲곕낯媛믪씠 紐⑤몢 ??紐⑤뜽??媛由ъ폒???쒕떎. 由대━????
1. 由대━???꾨낫 紐⑤뜽??**?섎굹濡??뺤젙**(湲곕낯媛? v-dd3abab9).
2. 洹?紐⑤뜽濡?P1 eval **?ъ깮??*(`reports/p1_release_legal_direct.json`쨌`reports/p1_release_holdout_direct.json` ??   `model_dir` == `CLASSIFIER_MODEL_DIR`). eval ?뚯씠?꾨씪?몄쑝濡쒕쭔 ?ъ깮???섍린 ?몄쭛 湲덉?).
3. `make operational-readiness` ??parity 寃뚯씠?멸? PASS ?몄? ?뺤씤(F1/FNR 由ы룷?멸? ?쇱씠釉?紐⑤뜽??湲곗닠).
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
