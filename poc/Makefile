# Koipa PoC — make targets
# 사용:
#   make venv                 # 의존성 설치
#   make poc-all              # PoC 5종 일괄 실행 (dryrun)
#   make infra-up / down      # docker compose 인프라
#   make api / worker         # 로컬 기동

PY ?= python
VENV_DIR ?= .venv
ACTIVATE = . $(VENV_DIR)/bin/activate
# uv — 의존성 락(uv.lock, 해시핀·유니버설) 생성/검사. 미설치 시 `pip install uv`.
UV ?= $(PY) -m uv

.PHONY: help venv install env lock lock-check infra-up infra-down infra-verify api worker beat \
        revalidate-pg-up revalidate-pg-migrate revalidate-pg-bench revalidate-pg-down revalidate-tsrank-crux \
        p2 p2-full bundle-dry licenses licenses-check sbom-publish analyze-synth verify-all \
        p3 p3-800 p4 p1 p5 acceptance-pack acceptance-pack-deps acceptance-test pilot-scorecard pilot-verify poc-all clean clean-data reports test-lite-cov \
        perf-scenarios perf-scenarios-full perf-clean-run \
        gold-set retrieval-gold check-data check-gold check-leakage check-release-evidence check-parser-support openapi-consistency check-manifest metamorphic-report human-review-queue eval-gold eval-p2-gold p1-retrain-dataset p1-eval-v3 p1-boundary p2-full-gold operational-readiness release-gate release-gate-recalculate release-manifest \
        recover-llm-judge \
        eval-synthetic eval-llm-gold eval-human-gold eval-all-tiers eval-trusted-ci \
        test-lite test-slow test-fullstack test-kl-integration test-model-download test-full

help:
	@echo "Targets:"
	@echo "  venv         create venv ($(VENV_DIR)) and install -e .[dev]"
	@echo "  env          copy .env.example -> .env if missing"
	@echo "  infra-up     docker compose up -d (postgres/minio/redis/mlflow)"
	@echo "  infra-down   docker compose down"
	@echo "  infra-verify python scripts/verify_infra.py"
	@echo "  api          run FastAPI locally (uvicorn)"
	@echo "  worker       run Celery worker locally"
	@echo "  p2           P2 embedding compare (dryrun, inmemory)"
	@echo "  p2-full      P2 KURE-v1+BGE-M3 × ES dense+hybrid (full 측정, ES 필요)"
	@echo "  bundle-dry   폐쇄망 번들 manifest dry-run (doc/12)"
	@echo "  licenses     OSS 라이선스 4종 산출 (txt/md/json/cyclonedx, doc/14)"
	@echo "  licenses-check  직접 의존성 라이선스 위험 검증 (CI용, exit 1 on risk)"
	@echo "  sbom-publish    licenses + doc/21_OSS_SBOM_보고서.html 자동 갱신 (D4)"
	@echo "  analyze-synth   합성 데이터셋 800건 종합 분석 (doc/02 §부록 D)"
	@echo "  verify-all   회신 무관 전체 검증 6단계 (pytest·PoC·migrate·bundle·license·analyze)"
	@echo "  p3           P3 synthesize 40 docs with noop provider + label check"
	@echo "  p3-800       P3 풀 합성 800건 (등급별 200×4, doc/05 계획)"
	@echo "  p4           P4 build corpus + extraction eval"
	@echo "  gold-set     gold set 생성 (test_set_v2 → datasets/gold/classification_gold.jsonl)"
	@echo "  check-data   데이터 품질 검사 — synthetic split + synthetic masked eval"
	@echo "  check-gold   gold_real/ 강화 검사 — label_source 3층 분리 리포트 포함"
	@echo "  eval-gold    P1 human_labeled_gold F1 (gold_real/ 구축 후 사용)"
	@echo "  eval-p2-gold P2 doc_id 기준 Recall@5 (oss_eval_queries.json)"
	@echo "  --- 3층 평가 (신뢰도 계층별 분리) ---"
	@echo "  eval-synthetic   ① synthetic_masked_eval  (gold/classification_gold.jsonl)"
	@echo "  eval-llm-gold    ② llm_judge_gold_eval    (gold_real의 llm_judge 건 한정)"
	@echo "  eval-human-gold  ③ human_review_gold_eval (gold_real의 human_review 건 한정)"
	@echo "  eval-all-tiers   위 3가지 순차 실행 + 통합 요약"
	@echo "  recover-llm-judge  uncertain_cases → llm_judge_primary 회수"
	@echo "  test-lite    빠른 테스트 (2분 이내, slow/fullstack/model_download 제외)"
	@echo "  test-full    전체 테스트 (인프라 + GPU 필요)"
	@echo "  p1           P1 classifier dryrun (synthetic_masked eval)"
	@echo "  p5           P5 E2E smoke (inproc)"
	@echo "  poc-all      P4 -> P3 -> P2 -> P1 -> P5 in order"
	@echo "  perf-scenarios       PSH 시나리오 성능 dryrun + doc/20 HTML (CI 호환, KPI 미달 시 fail)"
	@echo "  perf-scenarios-full  PSH 실측 (LLM·GPU·ES·PG 필요, KPI 미달은 warn)"
	@echo "  perf-clean-run       datasets·dist·__pycache__ 비우고 PSH 재실행 (환경 격차 사전 검증)"
	@echo "  reports      list generated reports"
	@echo "  clean-data   remove datasets/synthetic + datasets/p4_corpus + reports"

venv:
	$(PY) -m venv $(VENV_DIR)
	$(VENV_DIR)/bin/pip install -U pip
	$(VENV_DIR)/bin/pip install -e .[dev]

install:
	pip install -e .[dev]

# ── 의존성 락 (P1: 폐쇄망 번들 재현성·공급망 감사) ──────────────────────────
# uv.lock = 해시핀·유니버설(py3.11+ 전 플랫폼) 락. pyproject 를 바꾸면 `make lock` 재생성.
# CI 는 `make lock-check` 로 락이 pyproject 와 어긋나지 않았는지 강제(drift 시 fail).
lock: ## uv.lock 재생성 (pyproject 변경 후 실행)
	$(UV) lock

lock-check: ## uv.lock 이 pyproject 와 일치하는지 검사 (CI용, drift 시 exit 1)
	$(UV) lock --locked

env:
	@[ -f .env ] || cp .env.example .env

infra-up:
	docker compose up -d postgres minio redis mlflow

infra-down:
	docker compose down

infra-verify:
	$(PY) scripts/verify_infra.py

# ── ES→PG 단일화 재검증 (의사결정_대장 §03 게이트; infra/postgres/README.md) ──
# 기본 dev 스택과 격리된 pgvector+pg_bigm PG(:5433). ES 스택 비건드림.
REVALID_DB ?= postgresql+psycopg://koipa:koipa_dev@localhost:5433/koipa

revalidate-pg-up:
	docker compose -f docker-compose.pgvector.yml up -d --build

revalidate-pg-migrate:
	DATABASE_URL=$(REVALID_DB) $(PY) -m alembic upgrade head

revalidate-pg-bench:
	VECTOR_BACKEND=pg DATABASE_URL=$(REVALID_DB) $(PY) scripts/revalidate_pg_lexical.py

revalidate-pg-down:
	docker compose -f docker-compose.pgvector.yml down

# 크럭스: stock dev postgres(:5432)만으로 실 ts_rank 검증 — 커스텀 이미지(pgvector/pg_bigm) 불요.
# 게이트의 단일 핵심 불확실성(실 ts_rank ≈ 프록시 87%?)을 최소 비용으로 푼다.
revalidate-tsrank-crux:
	docker compose up -d postgres
	$(PY) scripts/revalidate_pg_tsrank_crux.py

api:
	uvicorn koipa.api.app:app --reload --host 0.0.0.0 --port 8000

worker:
	celery -A koipa.workers.celery_app worker -l info -Q classify,index,synthesis,learning,celery

beat:
	celery -A koipa.workers.celery_app beat -l info

p4:
	$(PY) scripts/build_p4_corpus.py
	$(PY) scripts/p4_extract_eval.py

p3:
	$(PY) scripts/p3_generate_synthetic.py --total 40 --provider noop --out datasets/synthetic/draft

p3-800:
	@# 별도 디렉터리 — make poc-all (smoke 40건, datasets/synthetic)과 분리
	rm -rf datasets/synthetic_800
	$(PY) scripts/p3_generate_synthetic.py --total 800 --provider noop \
	    --out datasets/synthetic_800

p2:
	$(PY) scripts/p2_compare_embeddings.py --mode dryrun --backends inmemory

p2-full:
	$(PY) scripts/p2_compare_embeddings.py --mode full --backends es --hybrid

bundle-dry:
	$(PY) scripts/build_offline_bundle.py --version 1.0.0-rc1 --dry-run \
	    --output dist/koipa-airgap-bundle

licenses:
	$(PY) scripts/dump_licenses.py --output licenses/

licenses-check:
	$(PY) scripts/dump_licenses.py --check --scope=direct

# D4 (2026-05-29): SBOM 4종 산출 + doc/21 HTML 자동 갱신.
# CI main 잡 + 로컬에서 `make sbom-publish`로 동시 호출 가능.
# doc/21 자동 commit은 별도 (blast radius 차단) — CI는 artifact 업로드만.
sbom-publish: licenses
	$(PY) scripts/render_sbom_html.py \
	    --in licenses/sbom.cyclonedx.json \
	    --out ../doc/21_OSS_SBOM_보고서.html

analyze-synth:
	@# 풀 합성(800건)이 있으면 그걸 분석, 없으면 smoke 40건 fallback
	@if [ -d datasets/synthetic_800 ]; then \
	    $(PY) scripts/analyze_synthetic.py --synth-dir datasets/synthetic_800; \
	else \
	    $(PY) scripts/analyze_synthetic.py; \
	fi

verify-all: ## 회신 무관 가능한 전체 검증 (pytest + dryrun PoC + 번들 + 라이선스 + 분석 + 데이터 품질)
	@echo "==[1/6]== pytest lite (slow/fullstack/model_download 제외)"
	@$(MAKE) -s test-lite
	@echo "==[2/6]== 데이터 품질 검사 (FAIL 시 abort)"
	@$(MAKE) -s check-data
	@echo "==[3/6]== PoC 5종 dryrun"
	@$(PY) scripts/run_all_pocs.py
	@echo "==[4/6]== 폐쇄망 번들 manifest dry-run"
	@$(MAKE) -s bundle-dry
	@echo "==[5/6]== OSS 라이선스 위험 (allowlist 모드)"
	@$(MAKE) -s licenses-check
	@echo "==[6/6]== 합성 800건 분석"
	@$(MAKE) -s analyze-synth
	@echo ""
	@echo "[verify-all] ALL GREEN"

gold-set: ## gold set 생성 (test_set_v2 → datasets/gold/classification_gold.jsonl)
	$(PY) scripts/make_gold_set.py

retrieval-gold: ## doc_id retrieval gold set 생성
	$(PY) scripts/make_retrieval_gold.py --source datasets/oss_eval_queries.json --out datasets/gold_real/retrieval_gold.jsonl --n 80

p1-retrain-dataset: ## P1 retraining JSONL build only
	$(PY) scripts/build_p1_retrain_dataset.py

# 온도 보정 — trainer 가 학습 직후 temperature.json 을 자동 산출(서빙 자동 로드)하지만,
# 재보정이 필요할 때 수동 실행. <CAL_MODEL_DIR>/val_logits.jsonl(학습 시 동봉)을 자동 사용.
CAL_MODEL_DIR ?= artifacts/classifier
calibrate: ## 학습된 모델의 val_logits 로 temperature.json 재산출 (CAL_MODEL_DIR=...)
	$(PY) scripts/calibrate_classifier.py --model-dir $(CAL_MODEL_DIR)

p1-boundary: ## Build focused P1 boundary report from the latest llm gold eval
	$(PY) scripts/build_p1_boundary_report.py \
	    --report reports/p1_release_holdout_direct.json \
	    --out reports/p1_boundary_report.md

# 운영 P1 평가 — 배포 모델(.env CLASSIFIER_MODEL_DIR과 일치해야 하며 readiness parity
# 게이트가 검증)을 두 티어로 평가해 operational-readiness가 읽는 리포트를 생성(단일 진실원).
# 법적근거 티어 = 신뢰 신호(게이트 판정), 전체 홀드아웃 = 노이즈 포함 보수적 하한.
# [2026-08-05] 기본값을 배포본(v5)으로 정정. 종전엔 v4(v-dd3abab9)를 가리켜, `make readiness` 를
# 그냥 돌리면 **배포하지 않는 모델의 리포트**가 생성되고 그게 번들 release-gate 입력이 됐다
# (실측: 번들은 v-fe4b386b 인데 readiness 는 v-dd3abab9 · 2026-07-06). 롤백 평가는 인자로 지정한다:
#   make p1-eval P1_MODEL=artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9
P1_MODEL ?= artifacts/classifier_p1_v5_clean/v-fe4b386b
DEPLOYED_MODEL ?= $(or $(CLASSIFIER_MODEL_DIR),$(P1_MODEL))
p1-eval: ## 배포 P1 모델을 gold 티어(법적근거+전체)로 평가 → readiness 입력 생성
	$(PY) scripts/eval_p1_model_gold.py --model-dir $(P1_MODEL) --mode direct \
	    --source-prior-cap S3 \
	    --label-source-filter public_definitive,koipa_case_based,nkt_designated \
	    --report reports/p1_release_legal_direct.md
	$(PY) scripts/eval_p1_model_gold.py --model-dir $(P1_MODEL) --mode direct \
	    --report reports/p1_release_holdout_direct.md

p1-eval-v3: ## [legacy] v3 모델 gold 티어 평가 — 운영 경로 아님(역사 비교용)
	$(PY) scripts/eval_p1_model_gold.py \
	    --model-dir artifacts/classifier_p1_retrain_v3/v-3443785f \
	    --mode direct \
	    --label-source-filter public_definitive,koipa_case_based,nkt_designated \
	    --report reports/p1_v3_public_gold_direct.md
	$(PY) scripts/eval_p1_model_gold.py \
	    --model-dir artifacts/classifier_p1_retrain_v3/v-3443785f \
	    --mode direct \
	    --label-source-filter llm_judge_primary,llm_judge_consensus,codex_review \
	    --report reports/p1_v3_llm_gold_direct.md

check-data: ## 데이터 품질 검사 — synthetic split + synthetic masked eval (exit 1 on FAIL)
	$(PY) scripts/check_data_quality.py \
	    --train datasets/labeled_v2_balanced/train.jsonl \
	    --test  datasets/gold/classification_gold.jsonl

check-leakage: ## holdout↔train 텍스트해시 누출 게이트 (exit 1 on FAIL; 데이터 위생)
	# 커밋된 해시 매니페스트(train_subset.jsonl 은 gitignore=CI 부재)로 실제 누출검사 수행 +
	# --strict-coverage 로 소스 부재 시 fake-green(무음 PASS) 대신 honest FAIL. 로컬은 원본 풀 fallback.
	$(PY) scripts/check_data_quality.py \
	    --train-hash-manifest datasets/gold_real/train_subset.hashes.txt \
	    --train-pool datasets/gold_real/train_subset.jsonl \
	    --holdout datasets/gold_real/holdout_eval.clean.jsonl,datasets/gold_real/holdout_business.clean.jsonl \
	    --strict-coverage \
	    --report reports/leakage_report.json

leakage-manifest: ## train 풀 → 누출 게이트용 텍스트해시 매니페스트(커밋 대상) 재생성
	$(PY) scripts/build_leakage_manifest.py \
	    --train-pool datasets/gold_real/train_subset.jsonl \
	    --out datasets/gold_real/train_subset.hashes.txt

check-parser-support: ## Parser support matrix must match extractor routes and optional extras
	$(PY) scripts/check_parser_support_matrix.py

openapi-consistency: ## OpenAPI yaml must match active FastAPI router paths
	$(PY) scripts/openapi_consistency.py --strict

llm-judge-dry: ## LLM judge 비용 추정 (실제 호출 없음)
	$(PY) scripts/make_llm_judge_gold.py --dry-run --n 200

llm-judge: ## LLM-as-judge gold set 생성 (ANTHROPIC_API_KEY 필요)
	$(PY) scripts/make_llm_judge_gold.py --provider anthropic --n 200 --min-llm-conf 0.7

llm-judge-local: ## LLM-as-judge (로컬 Qwen3/Ollama, 무료)
	$(PY) scripts/make_llm_judge_gold.py --provider local_openai --n 300

metamorphic-report: ## 후보 모델 대상 메타모픽 회귀 리포트 생성 → deploy_gate 경로에 물릴 JSON (로컬 Qwen/Ollama)
	## MODEL_DIR=<후보 모델> 로 후보를 지정. 산출 reports/metamorphic_pairs.json 을
	## KOIPA_DEPLOY_GATE_METAMORPHIC_REPORT_PATH 로 게이트에 연결(계약: 로더가 report 키 언랩).
	$(PY) scripts/gen_metamorphic_pairs.py \
	    --provider $(or $(PROVIDER),ollama) \
	    --model-dir $(or $(MODEL_DIR),artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9) \
	    --report reports/metamorphic_pairs.md

# Release 메타모픽 게이트: 커밋된 패러프레이즈 픽스처를 배포 모델로 재분류(live LLM 불요·결정적) →
# forward 미탐 회귀 OR 측정불가(표본<min_n) 시 fail-closed. deploy_gate 의 opt-in 가시적-skip 과 달리
# "리포트 미생성=조용한 green"을 막는다(배포전 ④). 배포 프로파일(onprem-local: T=3.0·escalation)에서 실행.
metamorphic-gate: ## Release 메타모픽 게이트 — 커밋 픽스처 재분류 → forward 회귀/측정불가 fail-closed (live LLM 불요)
	# 배포 모델(DEPLOYED_MODEL = CLASSIFIER_MODEL_DIR→P1_MODEL)로 재분류해 adversarial-gate·
	# readiness parity 와 동일 모델을 평가한다(다른 후보를 배포하며 v-dd3abab9 로 green 나는 구멍 차단).
	$(PY) scripts/gen_metamorphic_pairs.py \
	    --from-samples datasets/metamorphic/anchor_pairs.fixture.jsonl \
	    --model-dir $(or $(MODEL_DIR),$(DEPLOYED_MODEL)) \
	    --report reports/metamorphic_pairs.md
	$(PY) scripts/check_metamorphic_gate.py --report reports/metamorphic_pairs.json

check-gold: ## gold_real 강화 검사 — label_source 3층 분리 리포트 포함 (exit 1 on FAIL)
	$(PY) scripts/check_data_quality.py \
	    --train datasets/labeled_v2_balanced/train.jsonl \
	    --gold  datasets/gold_real/classification_gold.jsonl \
	    --gold-train-overlap-policy warn \
	    --report reports/gold_real_quality_report.json

check-release-evidence: ## Release eval evidence must be leakage-free and provenance-clean
	# 누출/overlap 은 커밋된 해시 매니페스트로 CI에서 실제 수행(원본 train_subset 은 gitignore) +
	# --strict-coverage 로 fake-green 차단. 원본 --train split 은 로컬에만 있으면 SKIP(비파괴).
	$(PY) scripts/check_data_quality.py \
	    --train datasets/gold_real/train_subset.jsonl \
	    --test datasets/gold_real/nohuman_proxy/proxy_eval_nohuman.jsonl \
	    --gold datasets/gold_real/nohuman_proxy/proxy_eval_nohuman.jsonl \
	    --train-hash-manifest datasets/gold_real/train_subset.hashes.txt \
	    --train-pool datasets/gold_real/train_subset.jsonl \
	    --holdout datasets/gold_real/nohuman_proxy/proxy_eval_nohuman.jsonl \
	    --strict-coverage \
	    --report reports/nohuman_proxy_quality_report.json
	$(PY) scripts/check_data_quality.py \
	    --train datasets/gold_real/train_subset.jsonl \
	    --test datasets/gold_real/holdout_eval.hardened.jsonl \
	    --gold datasets/gold_real/holdout_eval.hardened.jsonl \
	    --train-hash-manifest datasets/gold_real/train_subset.hashes.txt \
	    --train-pool datasets/gold_real/train_subset.jsonl \
	    --holdout datasets/gold_real/holdout_eval.hardened.jsonl \
	    --strict-coverage \
	    --report reports/hardened_holdout_quality_report.json

check-manifest: ## dataset_v1.0.yaml count/lineage consistency
	$(PY) scripts/check_dataset_manifest.py

human-review-queue: ## Build CSV queue for external human review import
	$(PY) scripts/build_human_review_queue.py \
	    --limit 40 \
	    --out datasets/corrections/human_review_queue.csv

eval-gold: ## P1 llm_judge_gold eval (gold_real/ 구축 후 사용 가능)
	$(PY) scripts/p1_train_classifier.py \
	    --mode dryrun \
	    --eval-type llm_judge_gold \
	    --report reports/p1_gold_real_eval_report.md

# ── 3층 평가 타겟 (신뢰도 계층별 분리) ──────────────────────────────────────

eval-synthetic: ## ① synthetic_masked_eval — gold/classification_gold.jsonl 기준
	$(PY) scripts/p1_train_classifier.py \
	    --mode dryrun \
	    --eval-type synthetic_masked \
	    --report reports/eval_synthetic_masked.md
	@echo "[tier-1] synthetic_masked_eval → reports/eval_synthetic_masked.md"

eval-llm-gold: ## ② llm_judge_gold_eval — gold_real의 llm_judge_primary/consensus/codex_review 한정
	$(PY) scripts/p1_train_classifier.py \
	    --mode dryrun \
	    --eval-type llm_judge_gold \
	    --label-source-filter llm_judge_primary,llm_judge_consensus,codex_review \
	    --report reports/eval_llm_judge_gold.md
	@echo "[tier-2] llm_judge_gold_eval → reports/eval_llm_judge_gold.md"

eval-human-gold: ## ③ human_review_gold_eval — gold_real의 human_review 건 한정 (없으면 N/A)
	$(PY) scripts/p1_train_classifier.py \
	    --mode dryrun \
	    --eval-type human_review_gold \
	    --label-source-filter human_review,public_definitive,koipa_case_based,nkt_designated \
	    --report reports/eval_human_review_gold.md
	@echo "[tier-3] human_review_gold_eval → reports/eval_human_review_gold.md"

eval-all-tiers: eval-synthetic eval-llm-gold eval-human-gold  ## 3층 순차 실행
	@echo ""
	@echo "=== 3층 평가 요약 ==="
	@$(PY) -c " \
import json, pathlib; \
tiers = [ \
  ('① synthetic_masked',   'reports/eval_synthetic_masked.json'), \
  ('② llm_judge_gold',     'reports/eval_llm_judge_gold.json'), \
  ('③ human_review_gold',  'reports/eval_human_review_gold.json'), \
]; \
[print(f'  {label}: n={d.get(\"n\",\"N/A\")}  F1={d.get(\"f1_macro\",\"N/A\")}  FNR_ts={d.get(\"fnr_by_grade\",{}).get(\"TS\",\"N/A\")}') \
 if (p := pathlib.Path(f)) and p.exists() and (d := json.loads(p.read_text(encoding=\"utf-8\"))) else print(f'  {label}: N/A (미실행)') \
 for label, f in tiers]"

eval-trusted-ci: ## 정직 홀드아웃 평가 — legally_grounded tier + bootstrap CI (소표본 과대해석 차단)
	## 배포 headline(FNR/F1)이 LLM심판 라벨 다수 위에 측정된 것을 티어별로 분리·CI로 노출.
	$(PY) scripts/eval_trusted_ci.py

active-learning: ## active learning 트리거 — corrections 현황 + 재학습 권고
	$(PY) scripts/check_active_learning.py --threshold 30

active-learning-file: ## DB 없이 파일 기반 corrections 확인
	$(PY) scripts/check_active_learning.py --file-only --threshold 30

recover-llm-judge: ## uncertain_cases → llm_judge_primary 회수 (S3→S2/S1 + conf>=0.85)
	$(PY) scripts/recover_llm_judge.py
	@$(MAKE) -s check-gold

import-review: ## 검수 CSV/JSONL → corrections/ + gold_real + DB document_labels
	@[ -n "$(INPUT)" ] || (echo "usage: make import-review INPUT=corrections.csv"; exit 1)
	$(PY) scripts/import_review_corrections.py $(INPUT) \
	    --merge-gold \
	    --write-db
	@$(MAKE) -s check-gold

import-review-dry: ## 검수 import dry-run (파일/DB 수정 없음)
	@[ -n "$(INPUT)" ] || (echo "usage: make import-review-dry INPUT=corrections.csv"; exit 1)
	$(PY) scripts/import_review_corrections.py $(INPUT) \
	    --merge-gold --write-db --dry-run

eval-p2-gold: ## P2 doc_id 기준 Recall@5 (oss_eval_queries.json 또는 retrieval_gold.jsonl)
	$(PY) scripts/p2_compare_embeddings.py \
	    --mode dryrun --backends inmemory \
	    --synth-dir datasets/oss_corpus \
	    --queries-file datasets/gold_real/retrieval_gold.jsonl \
	    --report-only \
	    --report reports/p2_gold_eval_report.md

p2-full-gold: ## KURE-v1 + ES dense/hybrid full eval on retrieval_gold
	$(PY) scripts/p2_compare_embeddings.py \
	    --mode full --models nlpai-lab/KURE-v1 \
	    --backends es --hybrid \
	    --synth-dir datasets/oss_corpus \
	    --queries-file datasets/gold_real/retrieval_gold.jsonl \
	    --chunk-size 1200 --chunk-overlap 100 \
	    --report reports/p2_gold_kure_es_hybrid_v3.md

operational-readiness: ## Build combined P1/P2/data readiness report (reads existing eval JSONs)
	$(PY) scripts/build_operational_readiness.py \
	    --model-dir $(P1_MODEL) \
	    --deployed-model $(DEPLOYED_MODEL) \
	    --out reports/operational_readiness.md

readiness: p1-eval operational-readiness ## 배포 모델 재평가 + readiness 빌드 (권장 진입점)

adversarial-gate: ## 적대 회귀 게이트 — golden_100 위험 미탐(고비밀→S3) baseline 초과 시 FAIL
	$(PY) scripts/eval_adversarial.py --model-dir $(or $(MODEL_DIR),$(DEPLOYED_MODEL))

release-gate: operational-readiness adversarial-gate metamorphic-gate ## Strict release gate (GA); requires every readiness gate to PASS
	$(PY) scripts/check_release_gate.py

# metamorphic-gate 는 pilot 에서도 prereq: forward 미탐 회귀(스타일 패러프레이즈에 고등급 강등)는
# 데이터천장(BLOCKED, waive 가능)이 아니라 안전 회귀(FAIL)이므로 pilot 도 통과 못한다.
release-gate-pilot: operational-readiness adversarial-gate metamorphic-gate ## Pilot release gate; blocks on FAIL (regression) but waives data-ceiling BLOCKED gates (human_review/parity) with an audited waiver
	$(PY) scripts/check_release_gate.py --allow-conditional

release-gate-recalculate: p1-eval p2-full-gold operational-readiness adversarial-gate metamorphic-gate ## Nightly/local strict gate with model + ES eval recomputed
	$(PY) scripts/check_release_gate.py

# 배포 경로 배선: 릴리스 산출물(매니페스트) 생성 전에 게이트를 강제한다. 하드 FAIL(진짜 회귀)은
# 차단하고, CONDITIONALLY_READY(human_review 데이터천장·model parity 대기)는 감사된 파일럿 waiver로
# 통과시킨다. GA용 strict 차단은 별도 `make release-gate`(--allow-conditional 없음).
release-manifest: operational-readiness p1-boundary metamorphic-gate ## Hash release evidence artifacts (blocked by the pilot release gate on a hard FAIL)
	$(PY) scripts/check_release_gate.py --allow-conditional
	$(PY) scripts/build_release_manifest.py \
	    --out reports/release_artifact_manifest.json

p1:
	$(PY) scripts/p1_train_classifier.py --mode dryrun

test-lite: ## 빠른 테스트 suite (2분 이내 목표, CI 기본 — slow/fullstack/model_download 제외)
	$(PY) -m pytest -q -m "not fullstack and not model_download and not slow"

test-lite-cov: ## test-lite + coverage 측정·리포트 (CI용 — 소프트, 하드 게이트 없음)
	$(PY) -m pytest -q -m "not fullstack and not model_download and not slow" \
	    --cov=koipa --cov-report=term-missing:skip-covered --cov-report=xml

test-slow: ## slow marker tests only
	$(PY) -m pytest -q -m slow

test-e2e-parse: ## 실문서 파싱→검수게이트→분류 E2E 회귀 (slow)
	$(PY) -m pytest -q tests/test_e2e_parse_classify.py

parse-probe: ## 실문서 파싱→게이트→분류 프로브 (예: make parse-probe ARGS="--gold")
	PYTHONPATH=src $(PY) scripts/parse_probe.py $(ARGS)

test-fullstack: ## fullstack marker tests only
	$(PY) -m pytest -q -m fullstack

test-kl-integration: ## KL 연동 계약(doc/19) + W3 라우터 + audit 통합 테스트 (slow·PG 필요)
	$(PY) -m pytest -q tests/test_kl_integration.py tests/test_api_routers_w3.py tests/test_audit_middleware.py

test-model-download: ## model_download marker tests only
	$(PY) -m pytest -q -m model_download

test-full: ## 전체 테스트 suite (인프라 + GPU 필요)
	$(PY) -m pytest -q

p5:
	$(PY) scripts/p5_e2e_smoke.py --mode inproc

# 고객 인수(acceptance) 샘플팩 — 상용 운영 포장. 전 포맷(TXT/PDF/DOCX/XLSX/XLS/PPTX/HWPX) × 등급,
# 공개+합성 혼합. acceptance-pack=생성(dev, 커밋 대상; PDF/XLS 는 reportlab/xlwt 설치 시), acceptance-test=
# in-proc 채점(severity floor veto: 파싱실패/숫자유실/고등급 미탐이면 exit 1; 정확도는 보고만).
acceptance-pack-deps: ## 인수팩 PDF/XLS 렌더용 dev-only 의존성(reportlab/xlwt) 설치 — 런타임/번들 불요
	$(PY) -m pip install -r scripts/requirements-docgen.txt
acceptance-pack: ## 인수 샘플팩 생성 → datasets/acceptance_pack/ (PDF/XLS 포함하려면 make acceptance-pack-deps 선행)
	$(PY) scripts/build_acceptance_pack.py
acceptance-test: ## 인수 샘플팩 채점 (in-proc parse→classify→gate; severity floor veto) — 고객은 bundle 의 run_acceptance.sh
# --require-model 필수. 없으면 가중치 미로드로 rule-fallback 이 돌아도 PASS 가 나온다
# (실측 2026-08-14: 그 상태로 "운영 경로 통과" 라고 보고할 뻔했다). deploy_cloud.sh 는
# 이미 이 플래그를 쓰는데 개발자용 진입점만 빠져 있었다.
# 가중치 경로는 CLASSIFIER_MODEL_DIR 로 준다 — 예: artifacts/classifier_p1_v5_clean/v-fe4b386b
	$(PY) scripts/run_acceptance.py --mode inproc --require-model
soak: ## W2 소크: 코퍼스 반복 투입(영속 서빙)로 메모리 누수·고등급 veto·escalation 안정성 검증
	$(PY) scripts/run_soak.py --cycles 50 --snapshot-every 10
pilot-scorecard: ## Render pilot scorecard HTML from acceptance/readiness JSON artifacts
	$(PY) scripts/render_pilot_scorecard.py
pilot-verify: ## Run pilot acceptance/readiness, always render scorecard, then return final verdict
	-$(PY) scripts/run_acceptance.py --mode inproc --require-model
	-$(PY) scripts/build_operational_readiness.py --model-dir $(P1_MODEL) --deployed-model $(DEPLOYED_MODEL) --out reports/operational_readiness.md
	$(PY) scripts/render_pilot_scorecard.py --fail-on-bad

poc-all: p4 p3 p2 p1 p5
	@echo "\nAll PoCs completed. See reports/"

# PSH — 시나리오 성능 보고서 (doc/19 S1~S8 × doc/20a KPI 매트릭스 → doc/20 HTML)
# 사용:
#   make perf-scenarios               # dryrun, KPI 미달 시 exit 1
#   make perf-scenarios-full          # full (LLM·GPU·ES·PG 필요)
#   make perf-scenarios FAIL_ON_MISS= # KPI 미달도 warn
FAIL_ON_MISS ?= --fail-on-miss
perf-scenarios:
	$(PY) scripts/run_perf_scenarios.py --mode dryrun $(FAIL_ON_MISS)

perf-scenarios-full:
	$(PY) scripts/run_perf_scenarios.py --mode full

# 환경 격차 사고 방지: datasets·dist·__pycache__ 비우고 PSH 재실행
# 메모리 패턴 — 로컬 누적 상태로 통과해도 CI clean에서 실패하는 경우 사전 검증
perf-clean-run:
	rm -rf datasets/synthetic datasets/p4_corpus reports/perf/perf_*.json dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	pip install -e ".[dev]"
	$(PY) scripts/run_perf_scenarios.py --mode dryrun --fail-on-miss

reports:
	@ls -la reports/ 2>/dev/null || echo "no reports yet"

clean-data:
	rm -rf datasets/synthetic datasets/p4_corpus reports

# P1-F3: incident runbook 자동화 타깃 — 야간/주말 1차 대응 단축
.PHONY: incident-api-down incident-queue-backlog incident-drift incident-audit-verify

incident-api-down:
	@echo "[incident] API 다운 1차 대응"
	@docker compose logs api --tail 200 || true
	@docker compose restart api || true
	@sleep 10
	@# 실제 엔드포인트는 /api/v1/healthz/live (프로세스 생존). 과거 /api/v1/health 는 존재하지
	@# 않아 재기동 성공 시에도 항상 curl 실패→오탐 에스컬레이션이었다.
	@curl -fsS http://localhost:8000/api/v1/healthz/live || echo "[FAIL] /healthz/live still down — escalate to AI팀"

incident-queue-backlog:
	@echo "[incident] Celery 큐 적체 1차 대응"
	@celery -A koipa.workers.celery_app inspect active || true
	@celery -A koipa.workers.celery_app inspect reserved || true
	@echo "[hint] worker 스케일 아웃: docker compose up -d --scale worker=8"

incident-drift:
	@echo "[incident] 모델 드리프트 점검"
	$(PY) -c "from koipa.services.drift_monitor import compute_drift; print('drift_monitor 모듈 OK — 실 임베딩 표본 필요')"

incident-audit-verify:
	@echo "[incident] 감사 로그 hash chain 검증"
	$(PY) -c "from koipa.services.audit_chain import verify_chain; r = verify_chain(); print('verified', r.verified, 'broken', r.broken, 'first_break', r.first_break_audit_id)"
