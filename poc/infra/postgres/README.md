# PG-native 벡터스토어 재검증 키트 (ES → Postgres 단일화, 의사결정_대장 §03 경로 ⓑ)

ES(nori+BM25)를 떼고 Postgres(pgvector dense + `ts_rank`/`pg_bigm` 어휘)로 갈 수 있는지
**실 PG 연산자로 확정**하기 위한 격리 스택. 기본 dev 스택(`docker-compose.yml`, ES 포함)을
건드리지 않는다(별도 이름·포트 :5433).

## 배경 (왜 재검증인가)
프록시 벤치(`scripts/_bench_pg_lexical_revalidation.py`, 파이썬 인프로세스 BM25) + 적대검증 결과:
- **토큰화는 위험 아님** — 형태소(≈nori)·bigram·trigram이 채점 고정 시 동급.
- **IDF 채점이 위험** — PG 코어 `ts_rank`는 IDF 없음(실측 ~80–85%), raw `pg_bigm`/`pg_trgm`
  유사도는 62–71%로 붕괴. nori 동급(92–94%)은 BM25 확장 또는 앱사이드 채점 必.
- 프록시의 92–94%는 **스크립트 자체 IDF-BM25**라 PG 네이티브 아님 + gold 쿼리가 **발췌**라
  형태소 이점이 가려짐 → **실 PG 연산자 × 자연어(비발췌) 쿼리** 재검증이 ES 폐기 전 게이트.

## 절차
```bash
# 0) 이미지 빌드 + 기동 (pgvector 내장 + pg_bigm 소스빌드; 빌드 타임 인터넷 필요)
docker compose -f docker-compose.pgvector.yml up -d --build

# 1) 스키마 (tb_rag_vectors / tb_rag_aliases + vector/pg_bigm 확장)
DATABASE_URL=postgresql+psycopg://lloydk:lloydk_dev@localhost:5433/lloydk \
  alembic upgrade head

# 2) 확장 확인
docker compose -f docker-compose.pgvector.yml exec postgres-pgvector \
  psql -U lloydk -d lloydk -c "\dx"   # vector, pg_bigm 보여야 함

# 3) 실 PG 연산자 벤치 (코퍼스 적재 + dense/hybrid/raw-pg_bigm Recall)
VECTOR_BACKEND=pg \
DATABASE_URL=postgresql+psycopg://lloydk:lloydk_dev@localhost:5433/lloydk \
  python scripts/revalidate_pg_lexical.py
```

## 합격 게이트 (경로 확정 기준)
- **자연어(비발췌) 쿼리** 세트에서 `hybrid ts_rank+pg_bigm` **R@5 가 nori ~94% 대비 ±5pp 이내**
  → **경로 ⓑ 확정**(현 스캐폴드 채택, ES 폐기 진행).
- **>10pp 퇴행** → **경로 ⓒ 승급**: BM25 확장(ParadeDB/pg_search·VectorChord-bm25) 또는 앱사이드 BM25.
- `retrieval_gold`(발췌)는 1차 sanity(프록시 대조)용 — 판정은 NL 쿼리로.
  NL 쿼리 세트는 동일 문서에 대한 LLM 재구성 질문으로 별도 생성(`--queries <nl.jsonl>`).

## 폐쇄망 주의
- `pg_bigm`은 빌드 타임에 소스 다운로드(인터넷 필요). **빌드는 인터넷 환경에서 1회**,
  산출 이미지(`lloydk/postgres:16-pgvector-pgbigm`)를 오프라인 번들에 동봉 → 런타임 인터넷 불요.
- 게이트 통과 시 본 이미지를 메인 `docker-compose.yml`의 postgres로 승격하고
  ES 제거(전체 변경 리스트는 PR 설명/의사결정 참조).

## 구성 파일
- `Dockerfile.pgvector` — pgvector(베이스 내장) + pg_bigm(소스빌드)
- `../../docker-compose.pgvector.yml` — 격리 스택(:5433)
- `../../alembic/versions/a1b2c3d4e5f6_pg_rag_vectorstore.py` — 스키마
- `../../src/lloydk/adapters/vectorstore/pg_store.py` — PgVectorStore (route ⓑ)
- `../../scripts/revalidate_pg_lexical.py` — 실 PG 연산자 벤치
