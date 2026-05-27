# Lloydk PoC — make targets
# 사용:
#   make venv                 # 의존성 설치
#   make poc-all              # PoC 5종 일괄 실행 (dryrun)
#   make infra-up / down      # docker compose 인프라
#   make api / worker         # 로컬 기동

PY ?= python
VENV_DIR ?= .venv
ACTIVATE = . $(VENV_DIR)/bin/activate

.PHONY: help venv install env infra-up infra-up-rollback infra-down infra-verify api worker \
        p2 p2-full migrate-dry bundle-dry licenses licenses-check analyze-synth verify-all \
        p3 p3-800 p4 p1 p5 poc-all clean clean-data reports

help:
	@echo "Targets:"
	@echo "  venv         create venv ($(VENV_DIR)) and install -e .[dev]"
	@echo "  env          copy .env.example -> .env if missing"
	@echo "  infra-up     docker compose up -d (postgres/elasticsearch/minio/redis/mlflow)"
	@echo "  infra-up-rollback  docker compose --profile rollback up -d (+qdrant 롤백 가동)"
	@echo "  infra-down   docker compose down"
	@echo "  infra-verify python scripts/verify_infra.py"
	@echo "  api          run FastAPI locally (uvicorn)"
	@echo "  worker       run Celery worker locally"
	@echo "  p2           P2 embedding compare (dryrun, inmemory)"
	@echo "  p2-full      P2 KURE-v1+BGE-M3 × ES dense+hybrid (full 측정, ES 필요)"
	@echo "  migrate-dry  Qdrant→ES sample-5 dry-run (운영 데이터 무관 검증)"
	@echo "  bundle-dry   폐쇄망 번들 manifest dry-run (doc/12)"
	@echo "  licenses     OSS 라이선스 4종 산출 (txt/md/json/cyclonedx, doc/14)"
	@echo "  licenses-check  직접 의존성 라이선스 위험 검증 (CI용, exit 1 on risk)"
	@echo "  analyze-synth   합성 데이터셋 800건 종합 분석 (doc/02 §부록 D)"
	@echo "  verify-all   회신 무관 전체 검증 6단계 (pytest·PoC·migrate·bundle·license·analyze)"
	@echo "  p3           P3 synthesize 40 docs with noop provider + label check"
	@echo "  p3-800       P3 풀 합성 800건 (등급별 200×4, doc/05 계획)"
	@echo "  p4           P4 build corpus + extraction eval"
	@echo "  p1           P1 classifier dryrun (rule surrogate)"
	@echo "  p5           P5 E2E smoke (inproc)"
	@echo "  poc-all      P4 -> P3 -> P2 -> P1 -> P5 in order"
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
	docker compose up -d postgres elasticsearch minio redis mlflow

infra-up-rollback:
	docker compose --profile rollback up -d postgres elasticsearch qdrant minio redis mlflow

infra-down:
	docker compose down

infra-verify:
	$(PY) scripts/verify_infra.py

api:
	uvicorn lloydk.api.app:app --reload --host 0.0.0.0 --port 8000

worker:
	celery -A lloydk.workers.celery_app worker -l info

p4:
	$(PY) scripts/build_p4_corpus.py
	$(PY) scripts/p4_extract_eval.py

p3:
	$(PY) scripts/p3_generate_synthetic.py --total 40 --provider noop

p3-800:
	@# 별도 디렉터리 — make poc-all (smoke 40건, datasets/synthetic)과 분리
	rm -rf datasets/synthetic_800
	$(PY) scripts/p3_generate_synthetic.py --total 800 --provider noop \
	    --out datasets/synthetic_800

p2:
	$(PY) scripts/p2_compare_embeddings.py --mode dryrun --backends inmemory

p2-full:
	$(PY) scripts/p2_compare_embeddings.py --mode full --backends es --hybrid

migrate-dry:
	@# Qdrant 미가용·미설치 환경에서도 동작하도록 sample.jsonl + --skip-extract 사용
	@mkdir -p migration
	@printf '{"id":"d1","vector":[1.0,0.0,0.0,0.0],"payload":{"grade":"TS","text":"sample"}}\n' > migration/sample.jsonl
	@printf '{"id":"d2","vector":[0.0,1.0,0.0,0.0],"payload":{"grade":"S1","text":"sample"}}\n' >> migration/sample.jsonl
	$(PY) scripts/migrate_qdrant_to_es.py --collection sample --skip-extract --dry-run \
	    --target-index secrets-guides-koipa-test \
	    --alias secrets-guides-koipa --old-index secrets-guides-koipa-v0

bundle-dry:
	$(PY) scripts/build_offline_bundle.py --version 1.0.0-rc1 --dry-run \
	    --output dist/lloydk-airgap-bundle

licenses:
	$(PY) scripts/dump_licenses.py --output licenses/

licenses-check:
	$(PY) scripts/dump_licenses.py --check --scope=direct

analyze-synth:
	@# 풀 합성(800건)이 있으면 그걸 분석, 없으면 smoke 40건 fallback
	@if [ -d datasets/synthetic_800 ]; then \
	    $(PY) scripts/analyze_synthetic.py --synth-dir datasets/synthetic_800; \
	else \
	    $(PY) scripts/analyze_synthetic.py; \
	fi

verify-all: ## 회신 무관 가능한 전체 검증 (pytest + dryrun PoC + lint + 라이선스 + 분석)
	@echo "==[1/6]== pytest"
	@$(PY) -m pytest -q
	@echo "==[2/6]== PoC 5종 dryrun"
	@$(PY) scripts/run_all_pocs.py
	@echo "==[3/6]== Qdrant→ES 마이그 dry-run"
	@$(MAKE) -s migrate-dry
	@echo "==[4/6]== 폐쇄망 번들 manifest dry-run"
	@$(MAKE) -s bundle-dry
	@echo "==[5/6]== OSS 라이선스 위험 (allowlist 모드)"
	@$(MAKE) -s licenses-check
	@echo "==[6/6]== 합성 800건 분석"
	@$(MAKE) -s analyze-synth
	@echo ""
	@echo "[verify-all] ALL GREEN"

p1:
	$(PY) scripts/p1_train_classifier.py --mode dryrun

p5:
	$(PY) scripts/p5_e2e_smoke.py --mode inproc

poc-all: p4 p3 p2 p1 p5
	@echo "\nAll PoCs completed. See reports/"

reports:
	@ls -la reports/ 2>/dev/null || echo "no reports yet"

clean-data:
	rm -rf datasets/synthetic datasets/p4_corpus reports
