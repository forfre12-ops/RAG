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
        p2 p3 p4 p1 p5 poc-all clean clean-data reports

help:
	@echo "Targets:"
	@echo "  venv         create venv ($(VENV_DIR)) and install -e .[dev]"
	@echo "  env          copy .env.example -> .env if missing"
	@echo "  infra-up     docker compose up -d (postgres/qdrant/minio/redis/mlflow)"
	@echo "  infra-down   docker compose down"
	@echo "  infra-verify python scripts/verify_infra.py"
	@echo "  api          run FastAPI locally (uvicorn)"
	@echo "  worker       run Celery worker locally"
	@echo "  p2           P2 embedding compare (dryrun)"
	@echo "  p3           P3 synthesize 40 docs with noop provider + label check"
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
	docker compose up -d postgres qdrant minio redis mlflow

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

p2:
	$(PY) scripts/p2_compare_embeddings.py --mode dryrun

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
