# Phase 1 — 인프라 풀 부팅 + 로컬 LLM 라이브 연동

5070 Ti 풀가동 PoC의 Phase 1 결과. 4 인프라 컨테이너 + Ollama Qwen3 라이브 연동 + 첫 한국어 답안 검증 완료.

- 일자: 2026-05-30
- 프로파일: `onprem-local`

---

## 1.1 ES Nori 이미지 빌드 ✅

- `poc/infra/es/Dockerfile.es` 기반 빌드 (Phase 0 작성)
- 이미지: `lloydk/elasticsearch:8.15.3-nori`
- 빌드 시간: 약 1분
- 산출: `bin/elasticsearch-plugin install --batch analysis-nori` 자동 적용

---

## 1.2 4 인프라 컨테이너 부팅 ✅

```
lloydk-poc-postgres-1       Up (healthy)  5432
lloydk-poc-elasticsearch-1  Up (healthy)  9200
lloydk-poc-redis-1          Up            6379
lloydk-poc-minio-1          Up            9000, 9001
```

healthy 도달 시간: 약 30~40초 (cold boot).

### MLflow는 Phase 3으로 이행
- mlflow 컨테이너는 `psycopg2` 미포함으로 실패 (`ModuleNotFoundError`)
- Phase 1 핵심(인프라 + LLM)에는 불필요 → **Phase 3 학습 트래킹 시 별도 처리**
- 영향: 0건 (MLflow 의존 라우터·서비스 없음)

---

## 1.3 ES Nori 동작 + alembic + MinIO ✅

### Nori 한국어 형태소 분해 검증

```bash
POST http://localhost:9200/_analyze
{"analyzer":"nori","text":"영업비밀 핵심기술 검토"}
```

응답:
```json
{
  "tokens": [
    {"token":"영업","position":0},
    {"token":"비밀","position":1},
    {"token":"핵심","position":2},
    {"token":"기술","position":3},
    {"token":"검토","position":4}
  ]
}
```

→ "영업비밀 핵심기술 검토" 5 토큰 분해. plugin 정상 동작.

### Alembic Migration

- `DATABASE_URL=postgresql+psycopg://lloydk:lloydk_dev@localhost:5432/lloydk`
- `alembic current` → `a1f2c3d4e5f6 (head)` 도달
- 신규 마이그레이션 X (기존 head 유지)

### MinIO 버킷 초기화

```
[init_minio_buckets] created: lloydk-docs
[init_minio_buckets] created: lloydk-models
[init_minio_buckets] created: mlflow
```

→ 3 버킷 생성. `MINIO_ENDPOINT=localhost:9000` (스킴 없이) 형식 주의.

---

## 1.4 `.env` 작성 (onprem-local 활성화) ✅

- 이전 lite-noapi 설정은 `poc/.env.backup-lite-noapi`에 보존
- 신규 `.env`: `DEPLOY_PROFILE=onprem-local` + `LLM_PROVIDER=local_openai` + Ollama + BGE-M3 + ES + Postgres + MinIO + Redis

핵심:
```bash
DEPLOY_PROFILE=onprem-local
POC_MODE=full
LLM_PROVIDER=local_openai
LOCAL_LLM_BASE_URL=http://localhost:11434/v1
LOCAL_LLM_MODEL=qwen3:14b
EMBEDDING_PROVIDER=hf
EMBEDDING_MODEL=BAAI/bge-m3  # Phase 2에서 3-way 측정 후 1순위 확정
RERANKER_PROVIDER=bge
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
VECTOR_BACKEND=es
```

---

## 1.5 uvicorn 풀스택 부팅 + Qwen3 첫 실호출 ✅

### 부팅

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m uvicorn lloydk.api.app:app --host 127.0.0.1 --port 18030
```

- 워밍업: BGE-M3 + BGE-reranker-v2-m3 모델 로딩 (총 391 + 393 텐서)
- warmup_done=true 도달 시간: **약 82초**
- 첫 healthz 응답:

```json
{
  "status": "ok",
  "deploy_profile": "onprem-local",
  "embedding_provider": "hf",
  "llm_provider": "ollama",
  "vector_backend": "es",
  "reranker_provider": "bge",
  "warmup_done": true
}
```

### Qwen3 첫 한국어 /answer 실호출

**요청**: `query="영업비밀로 보호받기 위한 3 가지 요건은 무엇인가요?"`

**응답** (40.9초):
```
[1] 영업비밀로 보호받기 위해 정보는 비공개되어야 한다.
[2] 해당 정보가 경제적 가치를 가져야 하며,
[3] 비밀 유지에 대한 합리적인 조치가 취해져야 한다.
출처에 명시된 3가지 요건은 영업비밀의 핵심 조건으로...
```

| 지표 | 값 |
|---|---|
| HTTP | 200 |
| deterministic_fallback | **false** (진짜 LLM 답안) |
| 응답 시간 (e2e) | 40.9초 (콜드스타트 포함) |
| LLM 추론 latency | 25.8초 (Ollama 순수) |
| provider/model | ollama / qwen3:14b |
| input_tokens | 139 |
| output_tokens | 360 |
| citations | 0 (ES 미인덱싱 — Phase 2에서 해소) |

**의미**: 부정경쟁방지법 §2조 2호의 영업비밀 3 요건(비공지성·경제적 가치·비밀 관리)을 한국어로 정확히 출력. lite-noapi에서는 `deterministic_fallback=true`였으나 onprem-local에서 **진짜 Qwen3 답안 첫 사례**.

### /classify 라이브 확인

```
label: TS
confidence: 0.85
model_version: rule-fallback-v0
evidence count: 6
rag_context: 0
elapsed_ms: 1
```

→ 풀스택 위에서도 `/classify` 정상. confidence 0.85 고정은 학습 모델 미적용(Phase 3 대기). evidence 6건 정상 추출.

### 데모 콘솔

- `http://localhost:18030/demo/` 진입 → 10,731 bytes HTML 정상 로드
- nav 배지: `onprem-local · LLM ollama · emb hf` 표시

---

## 회귀 안정성

```
poc/tests/test_demo_page.py: 9/9 PASS (2.6s)
```

`.env` 전환에도 회귀 안전. ruff 0 위반 유지.

---

## Phase 1 핵심 성과

| 항목 | 결과 |
|---|---|
| 인프라 풀 부팅 | 4 컨테이너 healthy (mlflow Phase 3 이행) |
| ES Nori 한국어 형태소 분해 | ✅ 검증 |
| Postgres 마이그레이션 | ✅ alembic head |
| MinIO 버킷 | ✅ 3 버킷 생성 |
| onprem-local 프로파일 활성화 | ✅ .env 전환 |
| BGE-M3 임베딩 로드 | ✅ 82초 워밍업 |
| Qwen3 첫 한국어 답안 | ✅ 40.9초, deterministic_fallback=false |
| /classify 풀스택 동작 | ✅ TS·0.85·evidence 6 |
| 데모 콘솔 라이브 | ✅ onprem-local 배지 |
| 회귀 안정성 | ✅ 9/9 PASS |

---

## Phase 2 진입 준비도

| 사전 요건 | 상태 |
|---|---|
| ES 인덱스 매핑 표준 | poc/infra/es/index_template_secrets.json 존재 ✅ |
| 합성 5K + labeled 5K 데이터셋 | 준비됨 ✅ |
| KURE-v1 다운로드 스크립트 | poc/scripts/cache_kure_v1.py 존재 ✅ |
| dragonkue/BGE-m3-ko 다운 | Phase 2 첫 작업으로 처리 |
| P2 측정 스크립트 | poc/scripts/p2_compare_embeddings.py 풀 모드 지원 ✅ |
| BGE-M3 임베딩 라이브 동작 | ✅ Phase 1에서 검증 |

다음 단계: **Phase 2 — KURE/BGE-M3/dragonkue 3-way 임베딩 측정**.
