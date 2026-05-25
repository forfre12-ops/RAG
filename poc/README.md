# Lloydk AI Engine — PoC Scaffold

한국지식재산보호원 AI 영업비밀관리시스템 / 로이드케이 파트.
관련 설계서: `../doc/01~05_*.md` 및 `../doc/03_openapi_lloydk_kl.yaml`.

## 빠른 시작

```bash
# 1) 인프라 기동 (Postgres / Qdrant / MinIO / Redis / MLflow)
docker compose up -d postgres qdrant minio redis mlflow

# 2) API 빌드·기동
docker compose up -d api

# 3) Swagger UI
# http://localhost:8000/docs

# 4) 헬스 체크
curl http://localhost:8000/api/v1/healthz
```

## 디렉토리

- `docker-compose.yml` — 전체 PoC 스택
- `Dockerfile.api` — FastAPI + PyTorch + Transformers
- `src/lloydk/` — 애플리케이션 (모듈/어댑터/API)
- `scripts/` — 학습·추론·데이터 준비 CLI
- `datasets/` — PoC 데이터셋 (gitignore)
- `notebooks/` — 실험 노트북

## PoC 매핑

| PoC | 검증 | 진입점 |
|---|---|---|
| P1 | 분류 모델 (KF-DeBERTa) | `scripts/p1_train_classifier.py` |
| P2 | 임베딩·VectorDB | `scripts/p2_build_rag_index.py` |
| P3 | LLM 합성 품질 | `scripts/p3_generate_synthetic.py` |
| P4 | 문서 추출 | `scripts/p4_extract_eval.py` |
| P5 | E2E 스모크 | `scripts/p5_e2e_smoke.py` |

## 환경 변수

`.env.example` 참고. 최소 필요:
- `ANTHROPIC_API_KEY` (P3 합성용)
- `POSTGRES_PASSWORD`, `MINIO_ROOT_PASSWORD` 등 비밀번호
