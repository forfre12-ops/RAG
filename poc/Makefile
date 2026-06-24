# Lloydk PoC — make targets
# 사용:
#   make venv                 # 의존성 설치
#   make poc-all              # PoC 5종 일괄 실행 (dryrun)
#   make infra-up / down      # docker compose 인프라
#   make api / worker         # 로컬 기동

PY ?= python
VENV_DIR ?= .venv
ACTIVATE = . $(VENV_DIR)/bin/activate

.PHONY: help venv install env infra-up infra-down infra-verify api worker beat \
        revalidate-pg-up revalidate-pg-migrate revalidate-pg-bench revalidate-pg-down revalidate-tsrank-crux \
        p2 p2-full bundle-dry licenses licenses-check sbom-publish analyze-synth verify-all \
        p3 p3-800 p4 p1 p5 poc-all clean clean-data reports \
        perf-scenarios perf-scenarios-full perf-clean-run \
        gold-set retrieval-gold check-data check-gold check-manifest human-review-queue eval-gold eval-p2-gold p1-retrain-dataset p1-eval-v3 p1-boundary p2-full-gold operational-readiness release-gate release-manifest \
        recover-llm-judge \
        eval-synthetic eval-llm-gold eval-human-gold eval-all-tiers \
        test-lite test-slow test-fullstack test-model-download test-full

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
REVALID_DB ?= postgresql+psycopg://lloydk:lloydk_dev@localhost:5433/lloydk

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
	uvicorn lloydk.api.app:app --reload --host 0.0.0.0 --port 8000

worker:
	celery -A lloydk.workers.celery_app worker -l info -Q classify,index,synthesis,learning,celery

beat:
	celery -A lloydk.workers.celery_app beat -l info

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
	    --output dist/lloydk-airgap-bundle

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

p1-boundary: ## Build focused P1 boundary report from the latest llm gold eval
	$(PY) scripts/build_p1_boundary_report.py \
	    --report reports/p1_v3_llm_gold_direct.json \
	    --out reports/p1_boundary_report.md

# 운영 P1 평가 — 배포 모델(.env CLASSIFIER_MODEL_DIR과 일치해야 하며 readiness parity
# 게이트가 검증)을 두 티어로 평가해 operational-readiness가 읽는 리포트를 생성(단일 진실원).
# 법적근거 티어 = 신뢰 신호(게이트 판정), 전체 홀드아웃 = 노이즈 포함 보수적 하한.
P1_MODEL ?= artifacts/classifier_p1_retrain_v4_step3/v-f9b5cedb
p1-eval: ## 배포 P1 모델을 gold 티어(법적근거+전체)로 평가 → readiness 입력 생성
	$(PY) scripts/eval_p1_model_gold.py --model-dir $(P1_MODEL) --mode direct \
	    --label-source-filter public_definitive,koipa_case_based,nkt_designated \
	    --report reports/p1_step3_legal_direct.md
	$(PY) scripts/eval_p1_model_gold.py --model-dir $(P1_MODEL) --mode direct \
	    --report reports/p1_step3_holdout_direct.md

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
	$(PY) scripts/check_data_quality.py \
	    --train-pool datasets/gold_real/train_subset.jsonl \
	    --holdout datasets/gold_real/holdout_eval.jsonl,datasets/gold_real/holdout_business.jsonl \
	    --report reports/leakage_report.json

llm-judge-dry: ## LLM judge 비용 추정 (실제 호출 없음)
	$(PY) scripts/make_llm_judge_gold.py --dry-run --n 200

llm-judge: ## LLM-as-judge gold set 생성 (ANTHROPIC_API_KEY 필요)
	$(PY) scripts/make_llm_judge_gold.py --provider anthropic --n 200 --min-llm-conf 0.7

llm-judge-local: ## LLM-as-judge (로컬 Qwen3/Ollama, 무료)
	$(PY) scripts/make_llm_judge_gold.py --provider local_openai --n 300

check-gold: ## gold_real 강화 검사 — label_source 3층 분리 리포트 포함 (exit 1 on FAIL)
	$(PY) scripts/check_data_quality.py \
	    --train datasets/labeled_v2_balanced/train.jsonl \
	    --gold  datasets/gold_real/classification_gold.jsonl \
	    --report reports/gold_real_quality_report.json

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

active-learning: ## active learning 트리거 — corrections 현황 + 재학습 권고
	$(PY) scripts/check_active_learning.py --threshold 30

active-learning-file: ## DB 없이 파일 기반 corrections 확인
	$(PY) scripts/check_active_learning.py --file-only --threshold 30

recover-llm-judge: ## uncertain_cases → llm_judge_primary 회수 (S3→S2/S1 + conf>=0.85)
	$(PY) scripts/recover_llm_judge.py
	@$(MAKE) -s check-gold

import-review: ## 검수 CSV/JSONL → corrections/ + gold_real + DB document_labels
	@[ -n "$(INPUT)" ] || (echo "usage: make import-review INPUT=corrections.csv [TENANT=default]"; exit 1)
	$(PY) scripts/import_review_corrections.py $(INPUT) \
	    --merge-gold \
	    --write-db \
	    --tenant-id $${TENANT:-default}
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
	    --out reports/operational_readiness.md

readiness: p1-eval operational-readiness ## 배포 모델 재평가 + readiness 빌드 (권장 진입점)

adversarial-gate: ## 적대 회귀 게이트 — golden_100 위험 미탐(고비밀→S3) baseline 초과 시 FAIL
	$(PY) scripts/eval_adversarial.py

release-gate: operational-readiness adversarial-gate ## Strict release gate; requires every readiness gate to PASS
	$(PY) scripts/check_release_gate.py

release-manifest: operational-readiness p1-boundary ## Hash release evidence artifacts
	$(PY) scripts/build_release_manifest.py \
	    --out reports/release_artifact_manifest.json

p1:
	$(PY) scripts/p1_train_classifier.py --mode dryrun

test-lite: ## 빠른 테스트 suite (2분 이내 목표, CI 기본 — slow/fullstack/model_download 제외)
	$(PY) -m pytest -q -m "not fullstack and not model_download and not slow"

test-slow: ## slow marker tests only
	$(PY) -m pytest -q -m slow

test-fullstack: ## fullstack marker tests only
	$(PY) -m pytest -q -m fullstack

test-model-download: ## model_download marker tests only
	$(PY) -m pytest -q -m model_download

test-full: ## 전체 테스트 suite (인프라 + GPU 필요)
	$(PY) -m pytest -q

p5:
	$(PY) scripts/p5_e2e_smoke.py --mode inproc

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
	@curl -fsS http://localhost:8000/api/v1/health || echo "[FAIL] /health still down — escalate to AI팀"

incident-queue-backlog:
	@echo "[incident] Celery 큐 적체 1차 대응"
	@celery -A lloydk.workers.celery_app inspect active || true
	@celery -A lloydk.workers.celery_app inspect reserved || true
	@echo "[hint] worker 스케일 아웃: docker compose up -d --scale worker=8"

incident-drift:
	@echo "[incident] 모델 드리프트 점검"
	$(PY) -c "from lloydk.services.drift_monitor import compute_drift; print('drift_monitor 모듈 OK — 실 임베딩 표본 필요')"

incident-audit-verify:
	@echo "[incident] 감사 로그 hash chain 검증"
	$(PY) -c "from lloydk.services.audit_chain import verify_chain; r = verify_chain(); print('verified', r.verified, 'broken', r.broken, 'first_break', r.first_break_audit_id)"
