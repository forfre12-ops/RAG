# 벡터 DB 전환 계획서 — Qdrant → Elasticsearch

작성일: 2026-05-27
작성: 로이드케이 AI팀
대상: KOIPA / KL / Lloydk 내부
버전: **v0.9-final (구현 100% 완료 — KL E1~E9 회신 후 §13.1 절차로 v1.0 확정)**
상태: **제안 (Qdrant → Elasticsearch 전환 방향성 잠정 합의, 코드·테스트·문서 완비)**
근거: KL 사전 인터뷰에서 ES 운영 표준화 의사 확인, 최종 확정은 K1 + 부록 E1~E9 회신(기한 2026-06-09)으로 갈음

**구현 상태 (2026-05-27)**: §13.2의 S1·S2·S2.5·S4·S5 모두 완료. S3 실측만 회신·GPU·ES 클러스터 대기. v1.0 승격에 필요한 작업은 회신값 채우기뿐 (예상 2시간, §13.1).

---

## 1. 변경 요약

| 항목 | 기존 (v0) | 변경 (v1) |
|---|---|---|
| 벡터 DB | Qdrant 1.10+ (Rust, 단일 컨테이너) | **Elasticsearch 8.15+ (`dense_vector` + HNSW + BM25)** |
| 검색 방식 | 순수 dense 벡터 검색 (cosine) | **하이브리드 (dense kNN + BM25 + RRF 결합)** |
| 메타필터 | Qdrant `Filter` | **ES `bool/filter` + `term/range`** |
| 멀티 테넌트 | 컬렉션 또는 payload 필터 | **인덱스 분리 (`secrets-{tenant}-{version}`)** 또는 `tenant_id` 필터 |
| 클라이언트 | `qdrant-client` (Python) | **`elasticsearch[async]` 8.x (Python)** |
| 인프라 | Docker 단일 컨테이너 (6333/6334) | **ES 노드(9200) + 옵션 Kibana(5601)** |
| 운영 책임 | Lloydk 신규 구축 | **KL 기존 인프라 재사용 (운영 부담 감소)** |

---

## 2. 전환 배경

### 2.1 KL 사전 인터뷰 (K1 항목, 미회신 상태)
- KL 측 기존 운영 스택에 **Elasticsearch가 이미 도입돼 있음을 사전 인터뷰에서 시사**
- 운영 단일화·관제 통합·라이선스 일원화 목적으로 ES 채택 방향 잠정 합의
- **최종 확정은 K1 정식 회신(기한 2026-06-09) + 본 문서 §2.4 E1~E7 회신 수령 시점으로 갈음**

### 2.2 기술적 이득
1. **하이브리드 검색 표준화** — dense_vector(kNN) + BM25(키워드)를 한 쿼리로 RRF 결합 → 영업비밀 검색에서 키워드(예: "특허", "기밀") + 의미가 동시에 중요
2. **운영 도구 성숙도** — Kibana 대시보드·Watcher 알림·ILM(인덱스 생애주기 관리)·스냅샷 복원이 표준화
3. **보안 통합** — RBAC, document/field 레벨 보안, audit log → 영업비밀 시스템 감사 요구와 부합
4. **확장성** — 샤딩·복제 표준, 대용량 멀티 테넌트 분리에 유리

### 2.3 기술적 비용
1. **메모리 부담** — ES는 JVM heap + off-heap 캐시로 Qdrant 대비 메모리 사용 2~3배
2. **벡터 인덱싱 속도** — HNSW 구축 시간이 Qdrant 대비 다소 느림 (운영 영향 미미)
3. **단일 노드 운영 비추** — 최소 3노드 권장 → 발주처 인프라 확보 필요

### 2.4 전환 확정 전 KL 추가 확인 (E1~E9)

본 §2.4가 회신되어야 §1·§4·§11의 잠정 결정이 **v1.0으로 확정**됩니다.

| # | 질문 | 회신 시 채울 칸 | 영향 절 | 시급도 |
|---|---|---|---|---|
| **E1** | KL ES 버전 (`retriever` API 위해 8.14+ 권장, RRF 위해 8.12+ 필수) | 정확한 버전 (예: 8.15.2) | §4.3 | **최우선** |
| **E2** | `analysis-nori` 플러그인 설치 가능 + 사용자 사전 등록 권한 | 가능/불가 + 권한 범위 | §4.2, §7 | 높음 |
| **E3** | 라이선스 등급 (Basic / Platinum / Enterprise) — DLS·ML 가용 여부 결정 | 등급 명시 | §11.1 | **최우선** |
| **E4** | 멀티테넌트 격리 정책 (인덱스 분리 vs DLS) | 정책 명시 | §4.1, §11.3 | 높음 |
| **E5** | 운영 노드 수·메모리·디스크·JVM heap 사양 | 사양 표 | §3.3, §7 | 높음 |
| **E6** | 스냅샷 저장소(S3/NFS) 위치와 암호화 정책 | 경로 + 암호화 옵션 | §11.4 | 보통 |
| **E7** | Lloydk가 인덱스 생성·삭제·alias 스위칭 권한을 직접 갖는지 | 권한 범위 | §6 S5 | 보통 |
| **E8** | 영업비밀 문서 retention 정책 (보관 연한·삭제 기준) | docs/synth/audit 각 보존 기간 | §11.4 ILM | 보통 |
| **E9** | 운영 환경 망 등급 (L1 일반 / L2 망분리 / L3 완전폐쇄) + 반입 절차 | 등급 + 매체 + 심사 기간 | [doc/12 §1.2](12_폐쇄망_배포_설계.md) | **최우선** |

---

## 3. 영향도 분석

### 3.1 코드 변경 대상

| 파일 | 변경 내용 | 영향도 |
|---|---|---|
| `poc/src/lloydk/adapters/vectorstore/qdrant_store.py` | **삭제 → `es_store.py` 신규** | High |
| `poc/src/lloydk/adapters/vectorstore/__init__.py` | `build_store()` 분기 변경 (qdrant → es) | Med |
| `poc/src/lloydk/adapters/vectorstore/base.py` | **Protocol 확장** — `search_hybrid` 선택 메서드 추가 (§5.1), 호출부 무변경 보장 | Med |
| `poc/scripts/migrate_qdrant_to_es.py` | **신규** — Qdrant scroll → JSONL → ES `_bulk` (§6 S2.5) | Med |
| `poc/src/lloydk/adapters/vectorstore/inmemory_store.py` | **유지** (dryrun 폴백) | None |
| `poc/src/lloydk/config.py` | `qdrant_url` → `es_url`, `es_username`, `es_password`, `es_verify_certs` | Med |
| `poc/pyproject.toml` | `qdrant-client` 제거 → `elasticsearch[async]>=8.15` 추가 | Low |
| `poc/docker-compose.yml` | `qdrant` 서비스 제거 → `elasticsearch` + 옵션 `kibana` 추가 | Med |
| `poc/scripts/p2_compare_embeddings.py` | L66 `build_store(force_memory=True)` → `build_store()`(full) / dryrun은 현행, 하이브리드 RRF 옵션 추가 | Med |
| `poc/scripts/verify_infra.py` | ES 헬스체크로 교체 | Low |
| `poc/Makefile` | `qdrant` 타깃 → `es` 타깃 | Low |
| `poc/README.md` | 인프라 안내 갱신 | Low |
| `.env` (각 환경) | `QDRANT_URL` 제거 → `ES_URL`, `ES_USERNAME`, `ES_PASSWORD` | Low |

**Protocol 추상(`VectorStore`)이 유지되므로 호출부(서비스/모듈) 코드는 무변경.** 어댑터 교체만으로 마무리됩니다.

### 3.2 문서 변경 대상

| 문서 | 변경 내용 |
|---|---|
| `doc/02_기술스택_확정_및_PoC_계획.md` | §1.4 벡터 DB 비교 매트릭스 결과 갱신, "Qdrant 1순위" → "Elasticsearch 확정" |
| `doc/04_AI코어_모듈_상세설계.md` | §9.2 VectorDB Adapter 구현체 갱신, RAG Indexer/Retriever 다이어그램 |
| `doc/06_협의요청서_KL_발주처.md` | K1 영향 문구 갱신, K5 멀티테넌트 절 ES 인덱스 분리 정책 반영 |
| `doc/07_전체설계_통합본.md` / `.html` | 인프라 다이어그램 갱신 |
| `doc/05_PoC_데이터셋_구축계획.md` | P2 PoC 평가 환경 ES로 갱신 |
| `doc/00_인터뷰_워크플로우_전체그림.md` | 컴포넌트 다이어그램 갱신 |
| `doc/08_인터뷰_모의질문_30선.md` | "왜 ES인가" 답변 추가 |
| `doc/09_5분발표_스크립트.md` | 인프라 슬라이드 1줄 갱신 |
| `doc/06a_회신요청메일_초안.md` | K1 회신 반영 문구 |

### 3.3 인프라 변경

| 항목 | 기존 | 변경 |
|---|---|---|
| 포트 | 6333 (HTTP), 6334 (gRPC) | 9200 (HTTPS), 9300 (transport), 5601 (Kibana) |
| 볼륨 | `qdrantdata:/qdrant/storage` | `esdata:/usr/share/elasticsearch/data` |
| 메모리 권장 | 1~2 GB | **4 GB 이상 (JVM heap 2 GB + 캐시)** |
| 헬스체크 | `GET /healthz` | `GET /_cluster/health?wait_for_status=yellow` |

---

## 4. ES 인덱스·매핑 설계

### 4.1 인덱스 명명 규칙

```
secrets-{role}-{tenant}-{model}-{version}
```

- `role`: `guides` (FUN-002 가이드) | `docs` (운영 문서) | `synth` (FUN-003 합성)
- `tenant`: 단일은 `koipa`, 멀티는 기업 코드
- `model`: 임베딩 모델 식별자 (`kure-v1`, `bgem3`, `ksr`)
- `version`: 매핑/사전 변경 시 증분 (`v1`, `v2`...) → **무중단 재인덱싱 + alias 스위칭**

**alias 규약**: `secrets-{role}-{tenant}` → 항상 현재 운영 모델 인덱스를 가리킴. 호출부는 alias만 사용.

### 4.1.1 임베딩 모델·차원·인덱스 매핑

| 모델 | dim | 매핑 동등성 | 인덱스 예시 (KOIPA 단일) |
|---|---:|---|---|
| **KURE-v1** (기본) | 1024 | BGE-M3와 동일 매핑 (스왑 가능) | `secrets-guides-koipa-kure-v1` |
| BGE-M3 (폴백) | 1024 | KURE-v1과 동일 매핑 | `secrets-guides-koipa-bgem3-v1` |
| ko-sroberta (경량) | 768 | **별도 매핑 (dim 다름)** | `secrets-guides-koipa-ksr-v1` |

- 동일 차원(1024) 모델 간 교체는 **인덱스 데이터 보존 + 매핑 무수정**으로 가능 (재임베딩만 수행)
- 차원이 다른 모델로 교체 시 **별도 인덱스 신규 생성 → alias 스위칭** 필수
- 학습 시점·운영 시점에 어떤 임베딩 모델로 색인됐는지 추적용으로 매 청크의 `version` 필드에 `{model}-{version}` 기록

### 4.1.2 pgvector — 자체 결정으로 제거 (2026-05-27)

이전 버전에서 "2순위 도입 옵션"으로 명시했던 pgvector는 **자체 결정으로 완전 제거**되었습니다.

- **1순위 (단일)**: Elasticsearch 8.15.3 — 본 문서 §4.1, docker-compose 잠금
- **2순위 즉시 가용 롤백**: Qdrant — 본 계획서의 롤백 경로 (S5 종료 +4주까지 가동, `qdrant_store.py` 실재). `VECTOR_BACKEND=qdrant`로 즉시 전환 가능.

**제거 이유**:
1. `PgvectorStore` 어댑터 코드 0줄 — "폴백"이라 부를 자격이 없는 죽은 옵션
2. ES `dense_vector(int8_hnsw)` + BM25 + RRF 하이브리드로 기능 완전 중복
3. 자체 결정으로 K1(KL 인프라) = "우리 docker-compose 단일 스택"으로 잠금 → ES 거부 시나리오 발생 가능성 차단
4. `chunks.embedding vector(1024)` 컬럼·`CREATE EXTENSION vector`·HNSW 인덱스 모두 미사용 상태 — `postgres:16-alpine` 기본 이미지에는 pgvector 확장도 없어 실제로는 `init.sql` 적용 시 ERROR 가능성

**제거 범위** (2026-05-27 적용):
- `poc/migrations/init.sql` — `CREATE EXTENSION vector`·`chunks.embedding`·`idx_chunk_embedding_hnsw` 삭제
- `doc/07_DB_스키마_v2.sql` — 동일 정리
- `poc/scripts/verify_infra.py` — `pg_extension WHERE extname='vector'` 검증 제거
- `poc/src/lloydk/adapters/vectorstore/__init__.py` — docstring "pgvector(폴백)" 제거
- 본 문서·doc/02·04·08·10·15·00 — 모든 "pgvector 폴백" 언급 정리

**ES 완전 미가용 시 대응**: Qdrant 즉시 롤백 (`docker compose --profile rollback up -d qdrant` + `VECTOR_BACKEND=qdrant`). pgvector 신규 구현은 더 이상 옵션이 아님.

### 4.2 매핑 (KURE-v1 1024차원 기준)

> **dims 한도**: ES 8.11+에서 `dense_vector.dims` 최대 4096. KURE-v1(1024)·BGE-M3(1024)·ko-sroberta(768) 모두 안전 범위.

```json
{
  "settings": {
    "index": {
      "number_of_shards": 1,
      "number_of_replicas": 1,
      "refresh_interval": "5s",
      "analysis": {
        "tokenizer": {
          "korean_nori_tokenizer": {
            "type": "nori_tokenizer",
            "decompound_mode": "mixed",
            "user_dictionary": "userdict_ko.txt",
            "discard_punctuation": "true"
          }
        },
        "analyzer": {
          "korean_nori": {
            "type": "custom",
            "tokenizer": "korean_nori_tokenizer",
            "filter": [
              "lowercase",
              "nori_part_of_speech",
              "nori_readingform"
            ]
          }
        }
      }
    }
  },
  "mappings": {
    "properties": {
      "id":          { "type": "keyword" },
      "doc_id":      { "type": "keyword" },
      "chunk_idx":   { "type": "integer" },
      "tenant_id":   { "type": "keyword" },
      "grade":       { "type": "keyword" },
      "department":  { "type": "keyword" },
      "doc_type":    { "type": "keyword" },
      "version":     { "type": "keyword" },
      "created_at":  { "type": "date" },

      "text":        {
        "type": "text",
        "analyzer": "korean_nori",
        "search_analyzer": "korean_nori"
      },

      "embedding":   {
        "type": "dense_vector",
        "dims": 1024,
        "index": true,
        "similarity": "cosine",
        "index_options": {
          "type": "int8_hnsw",
          "m": 16,
          "ef_construction": 128
        }
      }
    }
  }
}
```

**선택 옵션**:
- `index_options.type`은 **`int8_hnsw`**(ES 8.13+, 메모리 약 75% 절감) 우선 채택. KL ES 버전이 < 8.13이면 `hnsw`로 폴백.
- `decompound_mode: "mixed"`로 복합어 원형·분해형을 동시 보존 → "기술자료임치" 같은 도메인 용어 검색 정확도 향상.
- `user_dictionary`(`userdict_ko.txt`)는 영업비밀 도메인 용어집(예: "기술자료임치", "영업비밀원본증명") — Git 관리·인덱스 reopen으로 reload.

### 4.3 하이브리드 검색 쿼리 (RRF)

**ES 8.14+ 권장 — `retriever` API (공식)**

```json
POST /secrets-guides-koipa/_search
{
  "size": 5,
  "retriever": {
    "rrf": {
      "rank_window_size": 50,
      "rank_constant": 20,
      "retrievers": [
        {
          "standard": {
            "query": {
              "bool": {
                "must":   [ { "match": { "text": "<query_text>" } } ],
                "filter": [
                  { "term":  { "tenant_id": "koipa" } },
                  { "terms": { "grade": ["1급", "특급"] } }
                ]
              }
            }
          }
        },
        {
          "knn": {
            "field": "embedding",
            "query_vector": [ /* 1024-d */ ],
            "k": 10,
            "num_candidates": 100,
            "filter": [
              { "term":  { "tenant_id": "koipa" } },
              { "terms": { "grade": ["1급", "특급"] } }
            ]
          }
        }
      ]
    }
  },
  "collapse": {
    "field": "doc_id",
    "inner_hits": { "name": "best_chunk", "size": 1 }
  }
}
```

**ES 8.12~8.13 폴백 — legacy `rank.rrf` 문법** (E1 회신 시 < 8.14면 자동 적용)

```json
POST /secrets-guides-koipa/_search
{
  "size": 5,
  "query": { "bool": { "must": [...], "filter": [...] } },
  "knn":   { "field": "embedding", "query_vector": [...], "k": 10, "num_candidates": 100, "filter": [...] },
  "rank":  { "rrf": { "rank_window_size": 50, "rank_constant": 20 } }
}
```

- ES 8.12 미만: 클라이언트단 RRF 폴백(§7 리스크 표 참조)
- 메타필터는 standard·knn **양쪽에 동시 적용** (kNN filter는 HNSW graph traversal에 사용)
- **`collapse: doc_id`**로 동일 문서의 다른 청크가 top-K에 중복 등장하는 것을 방지 → 검색 다양성 보장
- `num_candidates` 가이드:
  - 필터 selectivity ≥ 0.1: `max(100, k*20)`
  - 강필터(예: `grade=특급` 단일): `k*50` 이상 권장 — 후보 부족 시 recall 급락 위험 (§7 참조)

---

## 5. 어댑터 인터페이스 (Protocol 확장 + 폴리필)

기존 `VectorStore` Protocol에 `search_hybrid` **선택 메서드**를 추가하고, 비-ES 백엔드는 vec-only 폴리필을 제공 → 호출부는 항상 `vs.search_hybrid(...)`를 호출해도 안전.

### 5.1 Protocol 변경 (`base.py`)

```python
@runtime_checkable
class VectorStore(Protocol):
    name: str

    def ensure_collection(self, name: str, dim: int) -> None: ...
    def upsert(self, collection, ids, vectors, payloads=None) -> int: ...
    def search(self, collection, query, top_k=5, filter=None) -> list[SearchHit]: ...
    def count(self, collection: str) -> int: ...

    # 신규 (모든 구현체가 제공해야 함, 비-ES는 폴리필)
    def search_hybrid(
        self,
        collection: str,
        query_text: str,
        query_vec: Sequence[float],
        top_k: int = 5,
        filter: dict | None = None,
        **kwargs,
    ) -> list[SearchHit]: ...
```

### 5.2 구현체별 동작

| 구현체 | `search_hybrid` 동작 |
|---|---|
| **EsStore** | retriever API(8.14+) 또는 legacy `rank.rrf`(8.12~8.13) — BM25 + kNN + RRF |
| **QdrantStore** (롤백용) | `query_text` 무시, `query_vec`만으로 dense 검색 → `search()`에 위임 |
| **InMemoryStore** (dryrun) | 동일하게 `query_text` 무시, dense-only 폴리필 — **P2 PoC 한계: dryrun 모드는 BM25 기여도를 측정하지 못함** (§9에 명시) |

### 5.3 신규 어댑터 시그니처 (`es_store.py`)

```python
class EsStore:
    name = "elasticsearch"

    def __init__(
        self,
        url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        api_key: str | None = None,
        verify_certs: bool = True,
        ca_certs: str | None = None,
    ) -> None: ...

    def ensure_collection(self, name: str, dim: int) -> None: ...
    def upsert(self, collection, ids, vectors, payloads=None) -> int: ...    # _bulk
    def search(self, collection, query, top_k=5, filter=None) -> list[SearchHit]: ...
    def search_hybrid(self, collection, query_text, query_vec, top_k=5,
                       filter=None, rrf_window=50, rrf_constant=20,
                       use_retriever_api: bool | None = None) -> list[SearchHit]: ...
    def count(self, collection: str) -> int: ...
```

- `use_retriever_api=None`(기본) → 클러스터 버전 자동 감지 후 분기 (8.14+ retriever, 미만 legacy)
- `SearchHit.payload`에 ES `_source` 전체를 dict로 매핑

---

## 6. 마이그레이션 단계 (6단계)

| 단계 | 작업 | 산출물 | 기간 |
|---|---|---|---|
| **S1. 준비** | ES 매핑·index template 확정, docker-compose 구성, Nori 플러그인·사용자 사전, 시크릿 발급 | docker-compose.yml, .env.sample, `userdict_ko.txt` 초안 | 1일 |
| **S2. 어댑터 구현** | `es_store.py` + Protocol 확장 + 모든 구현체 폴리필 + 단위 테스트 | `EsStore`, `VectorStore` v2, 12개 테스트 (CRUD·필터·하이브리드·버전감지) | 3일 |
| **S2.5 데이터 마이그레이션 스크립트** | Qdrant `scroll` → JSONL → ES `_bulk` 변환 + count 일치 검증 + alias 스위칭 | `poc/scripts/migrate_qdrant_to_es.py` (운영 데이터 무존재 시 skip 가능, 운영 전환 시 필수) | 0.5일 |
| **S3. PoC 재실행** | P2 PoC를 ES로 재돌림 — KURE-v1 단독 vs KURE-v1+BM25+RRF 동일 코퍼스 직접 비교 | P2 리포트 v2 (Qdrant vs ES, dense vs hybrid 4-way 비교 표) | 1일 |
| **S4. 통합** | API/Worker 코드 `build_store(backend=...)` 분기 ES 기본화, qdrant_store 보존(롤백용) | PR + CI 통과 | 0.5일 |
| **S5. 문서·전파** | 9개 문서 갱신, KL·발주처 공유, 회신 메일 보강 | doc/* 갱신, 회신 메일 보강 | 0.5일 |

**총 소요**: 6.5일 (약 1.5주). 기존 4.5일 추정은 retriever API·Nori 사용자 사전·_bulk 에러 처리·alias 스위칭·버전 감지 분기 등을 과소평가한 것으로 판단되어 상향.

### 6.1 S2.5 데이터 마이그레이션 절차 (상세)

1. **추출**: Qdrant `scroll` API로 `(id, vector, payload)` 전건 export → `migration/{collection}.jsonl` (1만건/파일 분할)
2. **재임베딩 판정**: 임베딩 모델·차원이 동일하면 vector 그대로 재사용. 모델 교체 시 텍스트만 추출 후 새 모델로 재임베딩 (§4.1.1 매핑 표 기준)
3. **변환**: JSONL → ES `_bulk` NDJSON 포맷 (`{ "index": { "_id": ... } }` + payload+vector 한 줄씩)
4. **적재**: `_bulk` 호출 (배치 1000건, 에러 재시도 3회, 실패건은 `migration/failed.jsonl`로 격리)
5. **검증**: ES `_count` vs 원본 건수 일치 + 무작위 100건 sampling으로 `id`·`payload`·`vector` 동일성 확인 (벡터는 L2 거리 ≤ 1e-6)
6. **alias 스위칭**: 검증 통과 시 `POST /_aliases`로 alias를 신규 인덱스로 교체. 실패 시 alias 미변경 → 사용자 영향 없이 재시도
7. **역마이그레이션**: 롤백 필요 시 ES `scroll` → Qdrant `upsert` 역방향 스크립트 (동일 코드의 source/target 스왑, S2.5 산출물에 함께 포함)

**PoC 단계에서는 운영 데이터 0건 → 본 절차는 dry-run으로 5건 sample 데이터에 대해서만 검증** (E2E 흐름 확인 목적).

---

## 7. 리스크와 대응

| 리스크 | 영향 | 대응 |
|---|---|---|
| KL ES 버전이 < 8.14 (`retriever` API 미지원) | 권장 문법 사용 불가 | `EsStore`가 클러스터 버전 자동 감지 → legacy `rank.rrf` 문법(8.12~8.13)으로 폴백 |
| KL ES 버전이 < 8.12 (RRF 자체 미지원) | 하이브리드 검색 미가용 | 클라이언트단 RRF 구현 (BM25 결과 + kNN 결과를 코드에서 융합) — `EsStore.search_hybrid` 마지막 폴백 |
| ES 메모리 부족 (heap 2GB 미만) | OOM/지연 | JVM heap = 노드 메모리의 50%(최대 32GB) 권장. 4GB 미만 환경은 `int8_hnsw` 양자화 + `m`·`ef_construction` 축소 |
| Nori 토크나이저 미설치 | 한국어 검색 품질 저하 | `bin/elasticsearch-plugin install analysis-nori` 사전 설치, Dockerfile에 포함 |
| 폐쇄망 환경 | 플러그인·라이선스 파일 접근 불가 | analysis-nori·repository-s3 플러그인 zip + Platinum 라이선스 파일을 오프라인 번들에 포함 ([doc/12_폐쇄망_배포_설계.md](12_폐쇄망_배포_설계.md)) |
| 임베딩 차원 변경 (KURE-v1 → 다른 모델) | 인덱스 재생성 필요 | §4.1.1 모델·차원 매핑 표 기반으로 별도 인덱스 신규 생성 + alias 스위칭 (무중단) |
| 멀티테넌트 데이터 격리 | 정보 유출 위험 | **인덱스 분리(권장)** 또는 DLS(Platinum 필요, §11.1·E3 회신) |
| qdrant-client 코드 잔존 | 의존성·혼선 | S4에서 `qdrant_store.py` 즉시 삭제하지 않고 `_legacy/`로 격리, S5+4주 안정화 후 제거 |
| **라이선스 등급 오인** (Basic으로 DLS·ML 가용 가정) | 컴플라이언스 미충족·기능 미가용 | E3 회신 즉시 §11.1 매트릭스로 확정. Basic만 가능 시 인덱스 분리·RBAC로 우회 |
| **`num_candidates` 부족 → recall 저하** | 강필터(예: `grade=특급`) 시 후보 부족, hit miss | filter selectivity ≥ 0.1: `num_candidates = max(100, k*20)`, 강필터: `k*50`. EsStore가 filter 컨텍스트로 자동 산정 |
| **Nori 사용자 사전 누락** | 도메인 용어("기술자료임치"·"영업비밀원본증명") 분해 오류 | `userdict_ko.txt` Git 관리, 인덱스 reopen으로 reload, 분기별 갱신. 초안은 KOIPA 가이드 추출본으로 시작 |
| **청크 중복 노출** (동일 doc의 다중 청크가 top-K 점유) | 검색 다양성 저하 | `collapse: {field: doc_id, inner_hits: {...}}`로 doc-level 1순위 강제 (§4.3 쿼리 반영) |

---

## 8. 롤백 계획

### 8.1 `build_store()` 시그니처 변경

```python
# poc/src/lloydk/adapters/vectorstore/__init__.py
def build_store(
    *,
    backend: str | None = None,
    force_memory: bool = False,
) -> VectorStore:
    """
    backend: "es" (기본) | "qdrant" (롤백) | "inmemory" (dryrun)
            None이면 env VECTOR_BACKEND, 그것도 없으면 "es"
    force_memory: 테스트 강제 in-memory (backend 무시)
    """
    if force_memory:
        return InMemoryStore()

    backend = backend or os.getenv("VECTOR_BACKEND", "es")

    if backend == "inmemory":
        return InMemoryStore()
    if backend == "qdrant":
        from lloydk.adapters.vectorstore.qdrant_store import QdrantStore
        return QdrantStore()
    if backend == "es":
        try:
            from lloydk.adapters.vectorstore.es_store import EsStore
            return EsStore()
        except Exception as exc:
            warnings.warn(f"[vectorstore] ES unavailable: {exc}. Falling back to InMemory.",
                          RuntimeWarning, stacklevel=2)
            return InMemoryStore()
    raise ValueError(f"unknown VECTOR_BACKEND: {backend}")
```

### 8.2 호출부 변경

| 파일 | 변경 위치 | 변경 내용 |
|---|---|---|
| `poc/scripts/p2_compare_embeddings.py` | L66 `vs = build_store(force_memory=True)` | full 모드는 `build_store()` (env 따름), dryrun은 현행 유지 |
| `poc/src/lloydk/services/*.py` | 신규 인스턴스화 지점 | `build_store()` 호출, env로 backend 결정 |

### 8.3 롤백 절차

1. ES 장애 또는 합격선 미달 시: `export VECTOR_BACKEND=qdrant` → Qdrant 컨테이너로 즉시 복귀
2. S5까지 qdrant 컨테이너·코드 병행 가동 (docker-compose에 두 서비스 동시 정의, profile로 분리)
3. S5 종료 +2주 안정화 무사 통과 후 `qdrant_store.py`를 `_legacy/`로 이동 → 그 +2주 후 완전 삭제

---

## 9. PoC 합격선 (현행 유지, P2 v2 측정 후 재협의)

### 9.1 합격선 v0.9 결정

**기존 합격선을 그대로 유지**합니다. ES + RRF 전환만으로 합격선 상향을 단언하지 않습니다.

| 항목 | 합격선 (v0.9 유지) | 비고 |
|---|---|---|
| Recall@5 | ≥ 0.80 | KURE-v1 단독 기준 |
| NDCG@5 | ≥ 0.75 | KURE-v1 단독 기준 |
| 검색 지연 (p95) | ≤ 200 ms | KURE-v1 단독 / 하이브리드는 S3 측정 후 별도 판정 |
| 인덱싱 처리량 | ≥ 100 docs/s | _bulk 기준 |

### 9.2 합격선 상향 결정 절차 (S3 직후)

S3 PoC v2에서 **동일 코퍼스·동일 쿼리셋**으로 다음 4-way 비교를 측정한 뒤, 정량 데이터를 근거로 v1.0 합격선을 협의 확정합니다.

| 조합 | 측정 항목 |
|---|---|
| (a) Qdrant + KURE-v1 (현행 baseline) | Recall@5, NDCG@5, p50/p95 |
| (b) ES + KURE-v1 (dense kNN only) | 동일 |
| (c) ES + BM25 only | 동일 |
| (d) ES + KURE-v1 + BM25 + RRF (하이브리드) | 동일 |

- **(d)가 (a) 대비 Recall@5 +Δ ≥ 0.05** 입증되면 합격선 0.85로 상향
- **(d)의 p95가 (a) 대비 +50ms 이내**면 지연 합격선 220ms 유지, 그 이상이면 250~300ms로 완화 협의

### 9.3 합격선 상향의 부적절성 (현시점)

| 사유 |
|---|
| 1. 현재 P2 1차는 dryrun **hash baseline에서 Recall 0.70**만 측정. KURE-v1·BGE-M3 실측조차 없음 |
| 2. "RRF가 5~10%p 개선"이라는 통념은 영어 BEIR/MS MARCO 기준 — **한국어 영업비밀 도메인 정량 근거 없음** |
| 3. 검증 전 합격선 상향은 발주처 질의("왜 올렸냐") 시 답변 불가 → 신뢰 손상 |

→ 합격선 상향은 **S3 측정 후 발주처와 협의**해 정량 근거와 함께 v1.1에서 확정.

---

## 10. 의존성 변경

```diff
# poc/pyproject.toml
- "qdrant-client>=1.10",
+ "elasticsearch>=8.15",
```

`elasticsearch[async]` 변형은 FastAPI/uvicorn 비동기 경로용. 기본 클라이언트는 sync, Worker에선 async 사용.

---

## 11. 컴플라이언스·보안

### 11.1 라이선스 매트릭스 (Elastic 8.x 기준)

| 기능 | Basic (무료) | Platinum | Enterprise |
|---|:---:|:---:|:---:|
| TLS (전송 암호화) | ✅ (8.0+ 기본 활성) | ✅ | ✅ |
| RBAC (역할 기반 접근) | ✅ | ✅ | ✅ |
| API Key | ✅ | ✅ | ✅ |
| Audit Logging (file sink) | ✅ (8.0+) | ✅ | ✅ |
| **DLS (Document-Level Security)** | ❌ | ✅ | ✅ |
| **FLS (Field-Level Security)** | ❌ | ✅ | ✅ |
| SAML / OIDC / Kerberos | ❌ | ✅ | ✅ |
| IP Filtering | ❌ | ✅ | ✅ |
| ML (이상 탐지·Watcher) | ❌ | ✅ | ✅ |
| Searchable Snapshot | ❌ | ❌ | ✅ |
| Cross-Cluster Replication | ❌ | ❌ | ✅ |

**판정**:
- **영업비밀 시스템은 DLS 필요** (지원기업별 문서 격리) → **Platinum 이상 권장**
- KL이 Basic만 보유 시 → **인덱스 분리(`secrets-{tenant}-*`) + RBAC 역할 분리**로 우회 가능
- **E3 회신 즉시 본 매트릭스로 확정 결정**

### 11.2 전송·저장 암호화

- **전송**: HTTPS(9200) + 노드 간 transport TLS(9300) 필수 — `xpack.security.transport.ssl.enabled: true`
- **저장**: 디스크 전체 암호화(LUKS/dm-crypt) 권장. ES 자체 at-rest 암호화는 Enterprise 이상
- **스냅샷 암호화**: S3 SSE-KMS 또는 NFS 마운트 측 암호화 — `repository-s3` 플러그인 + KMS 키 ID 지정
- **시크릿 저장**: Elastic Keystore(`bin/elasticsearch-keystore`)로 `s3.client.default.access_key` 등 평문 노출 차단

### 11.3 RBAC·DLS·FLS

| 역할 | 권한 |
|---|---|
| `lloydk_writer` | `secrets-{tenant}-*` 인덱스 create/index/update |
| `lloydk_reader` | `secrets-{tenant}-*` 인덱스 read |
| `kl_admin` | 모든 테넌트 read, alias 스위칭 |
| `koipa_auditor` | 감사 로그 read-only |

- **DLS 사용 시**(Platinum): 단일 인덱스에 다중 테넌트 저장하고 `query: { term: { tenant_id: <user_tenant> } }`를 역할에 박아 강제
- **FLS 사용 시**: 등급별로 노출 필드 제한 — 예: `lloydk_reader` 역할은 `grade=특급` 문서에서 `text` 필드 숨김

### 11.4 ILM (인덱스 생애주기 관리)

| 인덱스 | hot | warm | cold | delete |
|---|---|---|---|---|
| `secrets-guides-*` (가이드, 영구) | 90일 | 1년 | 영구 보존 | 수동 |
| `secrets-synth-*` (합성문서, 단명) | 30일 | – | – | 자동 삭제 |
| `secrets-docs-*` (운영 문서) | E8 회신 후 확정 | E8 | E8 | E8 |
| ES audit log | 30일 | 90일 | – | 자동 삭제 |

- ILM policy는 `_ilm/policy/secrets-*`로 JSON 정의, index template과 연결
- snapshot 주기: 일 1회 / 보존 30일 (S3 또는 NFS, **E6 회신** 후 경로 확정)

### 11.5 폐쇄망 제약

- **플러그인 사전 반입**: `analysis-nori`·`repository-s3` zip 파일을 오프라인 번들에 포함 ([doc/12_폐쇄망_배포_설계.md](12_폐쇄망_배포_설계.md))
- **라이선스 활성화**: 폐쇄망에서도 Basic 자동 활성. Platinum 이상은 라이선스 파일 매체 반입 필요
- **비활성 기능 (폐쇄망 + Basic)**: ML, Watcher, Cross-Cluster Replication — 우리 워크로드엔 영향 없음
- **사용자 사전 갱신**: `userdict_ko.txt`는 운영망 내 Git 미러로 관리. 갱신 시 인덱스 reopen으로 reload
- **Sentry/원격 텔레메트리 차단**: `xpack.telemetry.enabled: false`, `xpack.monitoring.collection.enabled: false` (외부 호출 차단)

---

## 12. 결정 사항 요약 (v0.9 잠정 — E1~E7 회신 후 v1.0 확정)

1. **Qdrant → Elasticsearch 8.14+ 전환 방향 잠정 합의** (확정은 E1~E7 회신)
2. **하이브리드 검색(dense kNN + BM25 + RRF)은 opt-in 옵션** — 기본 검색 경로는 dense-only. RRF는 EsStore에서만 진짜로 동작하며(`HybridVectorStore` Protocol 만족), P2 측정에서 dense 대비 △Recall ≥ +0.05 입증 시 default 승격 협의. 한국어 영업비밀 도메인 정량 근거 부재(§9.3) 때문에 기본값화는 보류.
3. **VectorStore Protocol 이원화** (`VectorStore` + 마커 `HybridVectorStore`) — 비-ES 백엔드는 `search_hybrid` 호출 시 RuntimeWarning을 내며 dense-only로 폴백. 조용한 실패 차단.
4. **인덱스 분리 기반 멀티테넌트**, alias 스위칭으로 무중단 재인덱싱, 모델·차원별 인덱스 매핑 표 도입
5. **PoC 합격선은 현행 유지** (Recall@5 0.80, NDCG@5 0.75) — S3 4-way 측정 후 v1.1에서 상향 협의. (d)−(a) Recall ≤ +0.02 또는 p95 +50ms 초과 시 ES 전환 ROI 재평가 trigger.
6. **총 6.5일 (약 1.5주)**, qdrant 코드 S5+4주 안정화 후 제거
7. **pgvector는 자체 결정으로 제거** (§4.1.2 참조 — 어댑터 0줄·기능 ES와 중복), Qdrant가 유일한 즉시 가용 롤백 경로

---

## 13. 다음 액션

### 13.1 v0.9 → v1.0 승격 절차 (KL 회신 도착 시 즉시 실행)

회신 도착 → 다음 7단계를 **당일 처리** 권장:

| # | 작업 | 산출 | 예상 시간 |
|---|---|---|---|
| 1 | §2.4 표 9칸 회신값 직접 기입 | §2.4 갱신 | 5분 |
| 2 | E1(버전) → §4.3 retriever vs legacy 분기 확정 + EsStore `use_retriever_api` 기본값 결정 | §4.3 + es_store.py | 15분 |
| 3 | E3(라이선스) → §11.1 매트릭스 채움 + DLS 가용 여부 확정 → E4와 결합 | §11.1, §11.3 | 15분 |
| 4 | E5(노드 사양) → §7 리스크 표 (a/b/c 시나리오 확정) + JVM heap·HNSW 파라미터 docker-compose 반영 | §7, docker-compose | 30분 |
| 5 | E6·E8 → §11.4 ILM JSON 작성 + Snapshot 저장소 등록 | §11.4, ILM policy JSON | 30분 |
| 6 | E9(망 등급) → [doc/12 §1.2](12_폐쇄망_배포_설계.md) 갱신 + 매체 반입 절차 합의 | doc/12 v1.0 | 20분 |
| 7 | 헤더 `v0.9-final` → `v1.0` + git tag `es-transition-v1.0` | 본 문서 | 5분 |

**총 약 2시간 (best case)** — 회신값이 사전 가정 범위 안일 때.

**Worst case (3~5일)**: E1=8.10(retriever·legacy RRF 모두 미지원 → 클라이언트단 RRF만 가용, num_candidates·필터 설계 약화), E3=Basic(DLS 불가 → §11.3 RBAC·멀티테넌트 단일인덱스 설계 재작성), E5=2GB heap(int8_hnsw·m·ef_construction 전면 재튜닝 + p95 합격선 재협의), E9=L3 폐쇄망(Nori 플러그인·라이선스 매체 반입 절차 +6주). 회신 시나리오에 따라 일정 분기 예상.

⚠️ E3·E5·E9 회신 전엔 운영 파라미터 코드 freeze. 회신 도착 즉시 분기 판단 후 v1.0 확정 + KL/발주처 공유.

### 13.2 구현 액션 (모두 완료 ✅, 2026-05-27 기준)

- [x] S1: docker-compose.yml ES 구성·Nori 플러그인·`userdict_ko.txt` ([poc/infra/es/](../poc/infra/es/))
- [x] S2: `es_store.py` + `VectorStore` Protocol v2 + 26개 단위 테스트 ([poc/tests/test_es_store.py](../poc/tests/test_es_store.py))
- [x] S2.5: `migrate_qdrant_to_es.py` 5단계 + 19개 테스트 ([poc/scripts/migrate_qdrant_to_es.py](../poc/scripts/migrate_qdrant_to_es.py))
- [ ] **S3: P2 PoC v2 — 4-way 비교** (ES 클러스터 + GPU + 모델 다운로드 후, Q1·E1·E5 회신 의존)
- [x] S4: `build_store(backend=...)` 분기 + 호출부 (`p2_compare_embeddings.py`) 갱신
- [x] S5: 관련 문서 11개 일괄 갱신 ([doc/00·02·04·05·06·06a·07·08·09·10·12·14](.))

### 13.3 문서 정합성 추가 작업 (모두 완료 ✅)

- [x] [doc/02 §1.4](02_기술스택_확정_및_PoC_계획.md): Vector DB 비교 매트릭스 ES 1순위 재정렬 + P2 4-way 계획
- [x] [doc/04 §9.2](04_AI코어_모듈_상세설계.md): VectorDB Adapter Protocol v2·구현체 4종
- [x] [doc/06 K1-A](06_협의요청서_KL_발주처.md): E1~E9 부록 신설
- [x] [doc/03 OpenAPI](03_openapi_lloydk_kl.yaml): `rag_namespace` → `rag_index_alias` rename + ES 컨텍스트
- [x] [doc/10 위험관리대장](10_위험관리대장.md): R-E 7개 시나리오
- [x] [doc/12 폐쇄망 배포](12_폐쇄망_배포_설계.md): E9 망 등급 + vLLM 강제
- [x] [doc/14 OSS 라이선스](14_OSS_라이선스_보고서.md): PyMuPDF AGPL · MinIO AGPL · konlpy GPL 식별
- [x] [doc/11 운영 Runbook](11_운영_Runbook.md): 6개 핵심 시나리오 + RTO 4시간

### 13.4 회신 도착 후 단계별 후속 작업 (v1.0 이후)

| 단계 | 작업 | 의존 |
|---|---|---|
| S3 실측 | `make p2-full --backends es --hybrid` | Q1·E1·E5 + GPU |
| 합격선 협의 | S3 4-way 결과로 Recall@5 0.80 → 0.85 상향 가능성 발주처 협의 | S3 결과 |
| 폐쇄망 번들 빌드 | `build_offline_bundle.py` (dry-run X, 실제 빌드) → 매체 반입 | E9·운영 환경 확정 |
| Pgvector 어댑터 | R-E5 (c) 시나리오 발동 시에만 (ES 거부 fallback) | E5 회신 |
| 운영 데이터 마이그 | `migrate_qdrant_to_es.py` (실제) | 운영 데이터 존재 시 |
