# Lloydk AI Engine — PoC

한국지식재산보호원(KOIPA) AI 영업비밀관리시스템 / 로이드케이 파트.
관련 설계서: [`../doc/`](../doc/) (01~07).

## 빠른 시작 (Docker/GPU 없어도 동작)

```bash
# 1) Python 3.11 venv + 의존성
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# 2) .env 준비 (기본은 noop provider — API 키 불필요)
cp .env.example .env

# 3) PoC 5종 일괄 실행 (~2초)
python scripts/run_all_pocs.py
# 또는: make poc-all

# 4) 결과 확인
cat reports/summary.md
```

종합 PASS면 `reports/`에 P1~P5 개별 리포트(.md/.json)가 생성됩니다.

## 풀 인프라 실행 (협의·GPU 확보 후)

```bash
# Postgres / Qdrant / MinIO / Redis / MLflow 기동
make infra-up
python scripts/verify_infra.py     # 헬스 체크
python scripts/init_minio_buckets.py
python scripts/seed_keywords.py --db    # 키워드 시드 DB 적재

# API + Worker
make api      # uvicorn lloydk.api.app:app --reload
make worker   # celery -A lloydk.workers.celery_app worker -l info

# 또는 docker compose로 한 번에
docker compose up -d
```

## 디렉토리 구조

```
poc/
├── docker-compose.yml          # Postgres/Qdrant/MinIO/Redis/MLflow/api/worker
├── Dockerfile.api              # FastAPI + PyTorch + Transformers
├── Makefile                    # poc-all, infra-up, api, worker 등
├── migrations/init.sql         # PostgreSQL v2 스키마 (테넌트/파티셔닝/HNSW)
├── src/lloydk/
│   ├── adapters/               # 외부 의존성 교체 가능 계층
│   │   ├── llm/                #   noop/anthropic/openai/vllm
│   │   ├── embedding/          #   hash(드라이런)/KURE-v1/BGE-M3
│   │   ├── vectorstore/        #   inmemory(드라이런)/qdrant
│   │   └── storage/            #   local(드라이런)/minio
│   ├── modules/
│   │   ├── m1_synthesis/       # FUN-003 합성 문서 생성
│   │   ├── m2_preprocess/      # FUN-022 HWP/DOCX/PDF 추출·정규화·청크
│   │   ├── m3_labeling/        # FUN-023 룰 라벨러 + 키워드 시드 + LLM fallback
│   │   ├── m4_training/        # FUN-004 KF-DeBERTa 학습 (Trainer + MLflow)
│   │   └── m5_inference/       # FUN-005 청크 분류 + RAG + 룰 폴백
│   ├── services/               # ClassifyService, LLMUsageService
│   ├── schemas/                # Pydantic (Grade, ClassifyRequest/Response)
│   ├── api/                    # FastAPI (healthz, classify)
│   ├── workers/                # Celery 비동기 태스크
│   └── config.py               # pydantic-settings (.env 로드)
├── scripts/                    # PoC 진입점·시드·평가 CLI (아래 표)
├── tests/                      # 48개 pytest (어댑터/모듈/서비스/API)
└── datasets/                   # gitignore 대상 (raw/external/synthetic/p4_corpus)
```

## PoC 스크립트 매핑

| PoC | 검증 항목 | 합격선 | 진입점 |
|---|---|---|---|
| **P1** | KF-DeBERTa 분류 / 룰 surrogate | F1 ≥ 0.75, FNR ≤ 5% | `p1_train_classifier.py --mode dryrun\|full` |
| **P2** | KURE-v1 vs BGE-M3 검색 / hash baseline | Recall@5 ≥ 0.80, Lat ≤ 200ms | `p2_compare_embeddings.py --mode dryrun\|full` |
| **P3** | LLM 합성 + 라벨 일치 + 비용 | 라벨 일치 ≥ 90% | `p3_generate_synthetic.py --total 40 --provider noop\|anthropic` |
| **P4** | HWP/DOCX/PDF/MD 추출 | 누락 ≤ 5%, 품질 ≥ 0.7 | `build_p4_corpus.py` + `p4_extract_eval.py` |
| **P5** | API E2E 스모크 | 200 OK + 라벨 OK | `p5_e2e_smoke.py --mode inproc\|http` |

추가 스크립트:
- `run_all_pocs.py` — P4→P3→P2→P1→P5 일괄 실행 + `summary.md`
- `seed_keywords.py` — 40개 키워드 시드를 DB 또는 JSON으로 dump
- `p3a_eval_rule_labeler.py` — M3 룰 라벨러 자가검증 (12 시나리오)
- `init_minio_buckets.py` — MinIO 초기 버킷 생성
- `verify_infra.py` — Postgres/Qdrant/MinIO/Redis/MLflow 헬스

## 동작 모드 (POC_MODE)

| 모드 | 의미 | 사용 케이스 |
|---|---|---|
| **dryrun** (기본) | noop LLM + hash embedding + inmemory + local FS | Docker/GPU/API 키 없이 파이프라인·합격선 검증 |
| **full** | 실제 모델 로드 + 인프라 연결 | 발주처 데이터·GPU·API 키 확보 후 |

`.env`의 `LLM_PROVIDER`/`POC_MODE`/`EMBEDDING_MODEL` 만 바꾸면 동일 코드에서 두 모드 전환 가능.

## 테스트

```bash
pytest                  # 48개 (어댑터 + 모듈 + 서비스 + API)
pytest -k m3            # 라벨링만
pytest --tb=short -v    # 상세 실패 표시
```

CI는 `.github/workflows/poc-ci.yml` — 푸시/PR 시 pytest + PoC dryrun + 리포트 아티팩트 업로드.

## 환경 변수

`.env.example` 참고. dryrun 기본값은 모두 채워져 있어 키 없이 시작 가능.

| 키 | 용도 |
|---|---|
| `LLM_PROVIDER` | `noop`(기본) / `anthropic` / `openai` / `vllm` |
| `ANTHROPIC_API_KEY` | P3를 실제 Claude로 돌릴 때 |
| `EMBEDDING_MODEL` | `nlpai-lab/KURE-v1`(기본) / `BAAI/bge-m3` |
| `CLASSIFIER_BASE_MODEL` | `kakaobank/kf-deberta-base`(기본) / KoELECTRA |
| `POC_MODE` | `dryrun`(기본) / `full` |

## 1차 PoC 결과 (2026-05-26, dryrun)

- P4 추출: 누락 0.0% / 품질 0.986
- P3 합성: 라벨 일치 100% (40건), FNR 0%, $0
- P2 임베딩: hash baseline Recall 0.70 (실측은 full 모드)
- P1 분류: F1 1.0 / FNR 0% (룰 surrogate, 실측은 KF-DeBERTa 학습)
- P5 E2E: 4/4 라벨 일치, max 4ms (TestClient)

상세는 [`reports/summary.md`](reports/summary.md) 및 [`../doc/02_기술스택_확정_및_PoC_계획.md`](../doc/02_기술스택_확정_및_PoC_계획.md) 부록 A.
