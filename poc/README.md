# Lloydk AI Engine — PoC

한국지식재산보호원(KOIPA) AI 영업비밀관리시스템 / 로이드케이 파트.

설계 문서: 최신 외부 공유본은 [`../doc/result/open/index.html`](../doc/result/open/index.html)에서 시작한다. 아래 00~15 Markdown 링크 일부는 초기 PoC 산출물 경로라 현재 HTML 산출물과 다를 수 있다.

| 영역 | 문서 |
|---|---|
| **종합 진척 보고서 (회신 직전)** | [doc/15](../doc/15_진척_종합_보고서.md) ← **시작 권장** |
| 사업 개요·기능 분담 | [doc/01](../doc/01_프로젝트_개요_및_로이드케이_파트_설계.md) |
| 기술 스택·PoC 계획 + 부록 A·B·C | [doc/02](../doc/02_기술스택_확정_및_PoC_계획.md) |
| AI 코어 모듈 상세 | [doc/04](../doc/04_AI코어_모듈_상세설계.md) |
| 협의요청서 (Q1~Q7, K1~K5, E1~E9) | [doc/06](../doc/06_협의요청서_KL_발주처.md) |
| 전체 설계 통합본 | [doc/07](../doc/07_전체설계_통합본.md) |
| **위험관리대장 (31개 위험)** | [doc/10](../doc/10_위험관리대장.md) |
| **운영 Runbook (6개 시나리오, RTO 4h)** | [doc/11](../doc/11_운영_Runbook.md) |
| **폐쇄망 배포 설계** | [doc/12](../doc/12_폐쇄망_배포_설계.md) |
| **벡터DB ES 전환 계획서 v0.9-final** | [doc/13](../doc/13_벡터DB_ES_전환_계획서.md) |
| **OSS 라이선스 보고서 (AGPL/GPL 위험)** | [doc/14](../doc/14_OSS_라이선스_보고서.md) |

---

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

---

## 풀 인프라 실행 (협의·GPU 확보 후)

```bash
# Postgres(pgvector) / Redis 등 dev 인프라 기동
make infra-up
python scripts/verify_infra.py     # 헬스 체크
python scripts/init_minio_buckets.py
python scripts/seed_keywords.py --db    # 키워드 시드 DB 적재

# API + Worker (VECTOR_BACKEND=pg 기본, ES는 레거시)
make api      # uvicorn lloydk.api.app:app --reload
make worker   # celery -A lloydk.workers.celery_app worker -l info

# 또는 docker compose로 한 번에
docker compose up -d
```

---

## 디렉토리 구조

```
poc/
├── docker-compose.yml          # dev 스택(Postgres/Redis 등) + api/worker
├── Dockerfile.api              # FastAPI + PyTorch + Transformers
├── Makefile                    # poc-all, infra-up, p2-full, bundle-dry 등
├── alembic/versions/           # PostgreSQL 스키마 단일 진실 소스 (baseline + 변경분)
├── infra/es/                   # ES 인덱스 템플릿 + Nori 사용자 사전
│   ├── index_template_secrets.json
│   └── userdict_ko.txt
├── src/lloydk/
│   ├── adapters/               # 외부 의존성 교체 가능 계층
│   │   ├── llm/                #   noop/anthropic/openai/vllm
│   │   ├── embedding/          #   hash(드라이런)/KURE-v1/BGE-M3
│   │   ├── vectorstore/        #   pg(기본)/inmemory(dryrun)/es(레거시)
│   │   │                       #   Protocol에 search_hybrid 포함, 폴리필 일관
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
├── scripts/                    # PoC 진입점·시드·평가·번들·마이그 CLI (아래 표)
├── tests/                      # 126개 pytest (어댑터/모듈/서비스/API/마이그/번들)
└── datasets/                   # gitignore 대상 (raw/external/synthetic/p4_corpus)
```

---

## Make 타깃 한눈에

| 타깃 | 용도 | 회신 의존 |
|---|---|---|
| `make poc-all` | P4→P3→P2→P1→P5 dryrun 일괄 | ❌ |
| `make p2` | P2 임베딩 dryrun (inmemory) | ❌ |
| `make p2-full` | **P2 KURE/BGE hybrid 측정(레거시 ES 포함 가능)** | ✅ Q1+E1+E5 |
| `make p1` / `p3` / `p4` / `p5` | 개별 PoC | 일부 ✅ |
| `make infra-up` | postgres(pgvector)·minio·redis·mlflow 기동 | ✅ E5 |
| `make infra-verify` | 헬스체크 (postgres/minio/redis/mlflow) | ✅ E1·E2 |
| `make bundle-dry` | **폐쇄망 번들 manifest dry-run** | ❌ |
| `make api` / `worker` | 로컬 기동 | ❌ |

`make help`로 전체 목록.

---

## PoC 스크립트 매핑

| PoC | 검증 항목 | 합격선 (v0.9) | 진입점 |
|---|---|---|---|
| **P1** | KF-DeBERTa 분류 / 룰 surrogate | F1 ≥ 0.75, FNR ≤ 5% | `p1_train_classifier.py --mode dryrun\|full` |
| **P2** | KURE/BGE-M3 × PG pgvector+ts_rank hybrid | Recall@5 ≥ 0.80, Lat p50 ≤ 200ms | `revalidate_pg_lexical.py` / `p2_compare_embeddings.py --mode dryrun\|full --hybrid` |
| **P3** | LLM 합성 + 라벨 일치 + 비용 | 라벨 일치 ≥ 90% | `p3_generate_synthetic.py --total 40 --provider noop\|anthropic` |
| **P4** | HWP/DOCX/PDF/MD 추출 | 누락 ≤ 5%, 품질 ≥ 0.7 | `build_p4_corpus.py` + `p4_extract_eval.py` |
| **P5** | API E2E 스모크 | 200 OK + 라벨 OK | `p5_e2e_smoke.py --mode inproc\|http` |

추가 스크립트:
- `run_all_pocs.py` — P4→P3→P2→P1→P5 일괄 실행 + `summary.md`
- `seed_keywords.py` — 40개 키워드 시드 DB 또는 JSON dump
- `p3a_eval_rule_labeler.py` — M3 룰 라벨러 자가검증 (12 시나리오)
- `init_minio_buckets.py` — MinIO 초기 버킷 생성
- `verify_infra.py` — Postgres(pgvector) / MinIO / Redis / MLflow
- **`build_offline_bundle.py`** — 폐쇄망 자기완비 번들 빌더 (dry-run / 실 빌드)

---

## 동작 모드 (POC_MODE / VECTOR_BACKEND)

| 모드 | 의미 | 사용 케이스 |
|---|---|---|
| **dryrun** (기본) | noop LLM + hash embedding + inmemory + local FS | Docker/GPU/API 키 없이 파이프라인·합격선 검증 |
| **full** | 실제 모델 로드 + 인프라 연결 | 발주처 데이터·GPU·API 키 확보 후 |

| `VECTOR_BACKEND` | 효과 |
|---|---|
| `pg` (기본) | Postgres pgvector dense + bigram-tsvector ts_rank 하이브리드 (의사결정_대장 §03 ⓑ) |
| `inmemory` | dryrun / 강제 in-memory |
| `es` | (레거시) elasticsearch 패키지 별도 설치 시에만 |

`.env` 값만 바꾸면 동일 코드에서 모드·백엔드 전환. ES→PG 전환 근거는 의사결정_대장 §03 (실 PG 측정: 하이브리드 R@5 85% > dense 76%, ts_rank_cd 아닌 ts_rank+norm1).

---

## 테스트

```bash
pytest                  # 어댑터 + 모듈 + 서비스 + API + 번들
pytest -k es_store      # ES 어댑터만
pytest -k offline       # 폐쇄망 번들
pytest --tb=short -v    # 상세 실패 표시
```

CI는 `.github/workflows/poc-ci.yml` — 푸시/PR 시 pytest + PoC dryrun + 리포트 아티팩트 업로드.

---

## 환경 변수

`.env.example` 참고. dryrun 기본값은 모두 채워져 있어 키 없이 시작 가능.

| 키 | 용도 |
|---|---|
| `LLM_PROVIDER` | `noop`(기본) / `anthropic` / `openai` / `google` / `vllm` / `ollama` / `lm_studio` / `local_openai` |
| `ANTHROPIC_API_KEY` | 원격 — P3를 실제 Claude로 돌릴 때 |
| `OPENAI_API_KEY` | 원격 — OpenAI GPT-4o 사용 시 |
| `LOCAL_LLM_BASE_URL` | 로컬 — vLLM·Ollama·LM Studio OpenAI 호환 endpoint |
| `LOCAL_LLM_MODEL` / `LOCAL_LLM_API_KEY` | 로컬 모델명·인증 |
| `EMBEDDING_MODEL` | `nlpai-lab/KURE-v1`(기본) / `BAAI/bge-m3` |
| `CLASSIFIER_BASE_MODEL` | `kakaobank/kf-deberta-base`(기본) / KoELECTRA |
| `POC_MODE` | `dryrun`(기본) / `full` |
| **`VECTOR_BACKEND`** | `pg`(기본) / `inmemory` / `es`(레거시) |
| **`DATABASE_URL`** | Postgres(pgvector) — 트랜잭션 + 벡터스토어 겸용 |

### LLM 설정 시나리오 (W9 일반화 — 원격/로컬 자유 선택)

본 시스템은 KOIPA 외 다른 기관에도 일반화 납품되는 시스템입니다. LLM·임베딩은 환경에 따라 선택:

**시나리오 A — CI·테스트·dryrun** (가장 가벼움, API 키 불필요)
```bash
LLM_PROVIDER=noop
EMBEDDING_MODEL=nlpai-lab/KURE-v1   # sentence-transformers로 로컬 로드
```

**시나리오 B — GPU 미보유 + 원격 API 비용 허용** (운영 환경 일반)
```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-xxxxxxxx
LLM_MODEL=claude-sonnet-4-6
EMBEDDING_MODEL=nlpai-lab/KURE-v1   # CPU 추론 (느리지만 동작)
```

**시나리오 C — GPU 보유 + 운영비 0** (폐쇄망 권장)
```bash
# Ollama 가동: `ollama serve` + `ollama pull qwen3:14b`
LLM_PROVIDER=ollama
LOCAL_LLM_MODEL=qwen3:14b
EMBEDDING_MODEL=nlpai-lab/KURE-v1   # CUDA 가속
```

**시나리오 D — vLLM 자체호스팅** (대규모 처리량 필요)
```bash
# vLLM 서버 가동: `vllm serve Qwen/Qwen3-14B --port 8001`
LLM_PROVIDER=vllm
LOCAL_LLM_BASE_URL=http://localhost:8001/v1
LOCAL_LLM_MODEL=Qwen/Qwen3-14B
```

**폐쇄망 사전 캐시** — 모델 가중치를 미리 다운로드:
```bash
python scripts/cache_kure_v1.py        # KURE-v1 + BGE-M3 캐시
python scripts/cache_kure_v1.py --dry-run  # 캐시 존재 여부만 확인
```

---

## 회신 의존 작업 (KL E1~E9, 발주처 Q1~Q7)

회신이 도착하면 [doc/10 위험관리대장 §6](../doc/10_위험관리대장.md) 시나리오 매핑을 따라 즉시 1차 대응을 실행합니다. 회신 전까지는 다음 작업이 가능합니다:

| 작업 | 명령 | 검증 가능? |
|---|---|---|
| P2 dryrun 시뮬레이션 | `make p2 --hybrid` | ✅ ES SKIP 처리 |
| 폐쇄망 번들 manifest 검증 | `make bundle-dry` | ✅ |
| EsStore 단위 테스트 (mocked) | `pytest -k es_store` | ✅ |
| ES 매핑·인덱스 템플릿 검증 | `infra/es/index_template_secrets.json` | ✅ |
| 분류기 학습 dryrun (룰 surrogate) | `make p1` | ✅ |

---

## 1차 PoC 결과 (2026-05-26, dryrun)

- P4 추출: 누락 0.0% / 품질 0.986
- P3 합성: 라벨 일치 100% (40건), FNR 0%, $0
- P2 임베딩: hash baseline Recall 0.70 (실측은 full 모드 + E1 회신 후 4-way)
- P1 분류: F1 1.0 / FNR 0% (룰 surrogate, 실측은 KF-DeBERTa 학습 + Q1 회신)
- P5 E2E: 4/4 라벨 일치, max 4ms (TestClient)

상세는 [`reports/summary.md`](reports/summary.md) 및 [`../doc/02 §부록 A`](../doc/02_기술스택_확정_및_PoC_계획.md).

---

## 최근 주요 변경 (2026-05-27)

### 설계 문서 신규 5종
- **[doc/10](../doc/10_위험관리대장.md) 위험관리대장 (v0.9, 312줄)** — 31개 위험, 회신 변수 8개 시나리오 트리, 조합 위험 3종
- **[doc/11](../doc/11_운영_Runbook.md) 운영 Runbook (v0.9, 461줄)** — 장애 P0~P3 / 재학습 Canary / 모델·인덱스 롤백 / 백업 / DR (RTO 4시간)
- **[doc/12](../doc/12_폐쇄망_배포_설계.md) 폐쇄망 배포 설계 (v0.9, 535줄)** — 자기완비 번들 25~30GB, vLLM 강제, Blue/Green
- **[doc/13](../doc/13_벡터DB_ES_전환_계획서.md) ES 전환 계획서 (v0.9-final, 615줄)** — retriever API + Nori + int8_hnsw + 마이그 7단계 + 라이선스 매트릭스
- **[doc/14](../doc/14_OSS_라이선스_보고서.md) OSS 라이선스 보고서 (v0.9, 309줄)** — PyMuPDF·MinIO AGPL · konlpy GPL 위험 식별
- **[doc/15](../doc/15_진척_종합_보고서.md) 진척 종합 보고서 (v1)** — 회신 직전 인덱스 + 즉시 발동 가이드

### 코드·인프라
- **벡터 DB Elasticsearch 8.14+ 단일 백엔드** — `VectorStore` Protocol v2 + `EsStore` 3단 폴백 + Nori + int8_hnsw + alias
- **`build_offline_bundle.py`** — 폐쇄망 번들 manifest dry-run, doc/12 §3·§4 구현
- **OpenAPI 03 정합화** — `rag_namespace` → `rag_index_alias` rename + ES 컨텍스트 명시
- **pyproject 재구성** — 기본 23개 + 7개 extras (hwp·nlp·embedding·llm·orchestration·lint·full·dev) + dead deps 제거
- **CI 강화** — openapi-lint 잡 추가, bundle-dry 통합 (Qdrant migrate는 2026-05-27 v2 제거)
- **테스트 48 → 126** (어댑터 36 + 번들 23 신규, 0 회귀)

상세 진척과 회신 도착 시 발동 가이드: **[doc/15 종합 보고서](../doc/15_진척_종합_보고서.md)**

---

## Current Status

- P1 release candidate is the cleaned KF-DeBERTa model `artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9`.
- P2 retrieval operational config is KURE-v1 + PostgreSQL `pgvector` dense + bigram `ts_rank` hybrid + chunk_size=1200 / overlap=100. ES remains a legacy adapter, not the default production path.
- Retrieval gold is `datasets/gold_real/retrieval_gold.jsonl`; PG lexical revalidation is documented in `infra/postgres/README.md` and implemented by `scripts/revalidate_pg_lexical.py`.
- `make operational-readiness` builds the combined P1/P2/data readiness report. Current verdict is `CONDITIONALLY_READY`.
- `make release-gate` is stricter and currently blocks release until at least 40 `human_review` gold samples are added.
- `test-lite` is the fast non-fullstack suite; exact pass counts move as audit/regression tests are added.
- CI default pytest path now runs `make test-lite`; slow/fullstack/model_download suites are separated as explicit Make targets.
- `gold_real` now has 777 records: S3 420, S2 232, S1 70, TS 55. `human_review` is 1/40 and remains the main evidence gap.

## Release Gates (2026-07-05)

Before an operational release, run:

```bash
make check-manifest
make check-release-evidence
make human-review-queue
make p1-boundary
make p1-eval
make operational-readiness
make release-gate
make release-manifest
make p2-full-gold
```

Strict release requires all readiness gates to be `PASS`. Current blocker is external human review gold: `human_review=1/40`. Pre-human readiness is `CONDITIONALLY_READY`; P1 release-tier F1 now passes with source-prior policy applied to public-source records.
See [`RELEASE_CHECKLIST.md`](RELEASE_CHECKLIST.md) for the reviewer import, rollback, and final release sequence.
