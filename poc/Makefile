# Lloydk PoC — make targets
# 사용:
#   make venv                 # 의존성 설치
#   make poc-all              # PoC 5종 일괄 실행 (dryrun)
#   make infra-up / down      # docker compose 인프라
#   make api / worker         # 로컬 기동

PY ?= python
VENV_DIR ?= .venv
ACTIVATE = . $(VENV_DIR)/bin/activate

.PHONY: help venv install env infra-up infra-down infra-verify api worker \
        p2 p2-full bundle-dry licenses licenses-check analyze-synth verify-all \
        p3 p3-800 p4 p1 p5 poc-all clean clean-data reports \
        perf-scenarios perf-scenarios-full perf-clean-run

help:
	@echo "Targets:"
	@echo "  venv         create venv ($(VENV_DIR)) and install -e .[dev]"
	@echo "  env          copy .env.example -> .env if missing"
	@echo "  infra-up     docker compose up -d (postgres/elasticsearch/minio/redis/mlflow)"
	@echo "  infra-down   docker compose down"
	@echo "  infra-verify python scripts/verify_infra.py"
	@echo "  api          run FastAPI locally (uvicorn)"
	@echo "  worker       run Celery worker locally"
	@echo "  p2           P2 embedding compare (dryrun, inmemory)"
	@echo "  p2-full      P2 KURE-v1+BGE-M3 × ES dense+hybrid (full 측정, ES 필요)"
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
	docker compose up -d postgres elasticsearch minio redis mlflow

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

verify-all: ## 회신 무관 가능한 전체 검증 (pytest + dryrun PoC + 번들 + 라이선스 + 분석)
	@echo "==[1/5]== pytest"
	@$(PY) -m pytest -q
	@echo "==[2/5]== PoC 5종 dryrun"
	@$(PY) scripts/run_all_pocs.py
	@echo "==[3/5]== 폐쇄망 번들 manifest dry-run"
	@$(MAKE) -s bundle-dry
	@echo "==[4/5]== OSS 라이선스 위험 (allowlist 모드)"
	@$(MAKE) -s licenses-check
	@echo "==[5/5]== 합성 800건 분석"
	@$(MAKE) -s analyze-synth
	@echo ""
	@echo "[verify-all] ALL GREEN"

p1:
	$(PY) scripts/p1_train_classifier.py --mode dryrun

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
.PHONY: incident-api-down incident-es-yellow incident-queue-backlog incident-drift incident-audit-verify

incident-api-down:
	@echo "[incident] API 다운 1차 대응"
	@docker compose logs api --tail 200 || true
	@docker compose restart api || true
	@sleep 10
	@curl -fsS http://localhost:8000/api/v1/health || echo "[FAIL] /health still down — escalate to AI팀"

incident-es-yellow:
	@echo "[incident] ES yellow/red 1차 대응"
	@curl -s http://localhost:9200/_cluster/health?pretty || true
	@curl -s http://localhost:9200/_cat/shards | grep -E "UNASSIGNED|INITIALIZING" || echo "no problematic shards"

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
