"""Postgres 네이티브 벡터스토어 — pgvector(dense) + tsvector/ts_rank + pg_bigm(후보).

의사결정_대장 §03 경로 ⓑ(ts_rank 코어 + pg_bigm 후보)의 구현. 스키마는 마이그레이션
alembic a1b2c3d4e5f6 (tb_rag_vectors / tb_rag_aliases).

상태(2026-06-24): §03 결정으로 **기본 백엔드는 pg** 이며 ES 서비스는 core/prod/airgap
   compose 에서 제거됐다(build_store 기본값도 'pg'). 단위·정적 검증(test_pg_store)은 통과.
✅ 라이브 PG 정식 게이트 통과(2026-07-04, 적대검증 반영): docker-compose.pgvector.yml
   (pg_bigm+pgvector :5433)에 oss_corpus 3754건 KURE-v1 적재 후 revalidate_pg_lexical.py
   를 **자연어(비발췌) 190쿼리**(retrieval_gold_nl.jsonl)로 실행. 라이브 hybrid R@5=**85.3%**
   (162/190; TS94·S1 69·S2 86·S3 95) > dense(pgvector) 78%. **판정: 경로 ⓑ 확정** —
   근거는 -9pp가 아니다: 동일 NL 세트의 nori-프록시 hybrid ~86%(pg_lexical_revalidation_nl.json,
   dense+bigram 0.863·dense+morph 0.863)와 비교해 **≈-1pp(±5pp 내)**. (스크립트가 찍는
   "vs nori~94%"는 **발췌셋 lexical-only 프록시**라 비교불가 — ES/nori 제거로 스택 내 실측 불가.)
   ⚠️ 귀속 정정: 이 hybrid 는 **dense + bigram ts_rank(RRF k=60)** — search_hybrid 후보필터는
   tsv @@ to_tsquery(idx_ragvec_tsv)이고 **pg_bigm GIN/bigm_similarity 는 랭킹에 안 쓰인다**.
   +7pp(85 vs 78)는 bigram ts_rank 몫이지 pg_bigm 아님. 별도 raw pg_bigm arm(NL 26%·발췌 0%)은
   hybrid 가 안 쓰는 신호로, "랭커 금지·후보가속 전용" 설계 실증.
   ⚠️ 85.3%는 **낙관적 상한**: NL 쿼리가 verbatim 은 아니나(최장연속<10자) 키워드-free 아님
   (156/190이 타깃 고유명사 토큰 공유)·질의가 in-corpus 타깃 보장으로 작성 → 콜드스타트 개념검색은
   더 낮음. 비교 유효성 확인: collection='revalid' NULL 임베딩 0/3754(count(*) 가드가 못 잡는
   부분로드 없음). 리포트 reports/revalidate_pg_lexical.json.

설계 노트 (실측·적대검증 반영, scripts/_bench_pg_lexical_revalidation.py):
  - dense 는 pgvector 코사인(embedding <=> q). 저장소 무관(ES 와 동일 품질).
  - 어휘 채점은 **bigram 토큰 위의 ts_rank_cd(IDF 없음)** — NL(비발췌) 재검증 실측:
    bigram-ts_rank 87% ≈ nori-BM25 89%(-2pp), 반면 어절(plain tsvector 'simple')은
    NL 에서 47%로 붕괴(굴절/조사 변형). 그래서 tsv 는 content 가 아니라 **bigram_text**
    (앱사이드 _bigram)에서 생성하고, 질의도 같은 bigram 토큰화로 plainto_tsquery 한다.
    raw pg_bigm/pg_trgm 유사도를 **랭커로 쓰면 62-71%(NL 67/54)로 붕괴**하므로 금지
    (필요 시 pg_bigm GIN 은 후보생성 가속용으로만).
    nori 동급(BM25/IDF, ~94%)이 필요하면 BM25 확장(ParadeDB/pg_search·VectorChord-bm25)
    또는 앱사이드 BM25 로 업그레이드(=경로 ⓒ).
  - dense + 어휘 융합은 RRF(k=60) — es_store 와 동일 상수.

VectorStore / HybridVectorStore Protocol 충족. 컬렉션 = ES 인덱스 ↔ collection 컬럼.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Sequence

from koipa.adapters.vectorstore.base import SearchHit

logger = logging.getLogger(__name__)

_RRF_K = 60          # Cormack et al. 2009 표준 (es_store 와 통일)
_CAND_N = 50         # RRF 입력 후보 윈도우 (es_store _num_candidates_for 기본과 정합)
_EMBED_DIM = 1024    # KURE-v1 / BGE-M3 (마이그레이션 vector(1024) 와 일치)
_FILTER_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# payload 의 어느 키를 전용 컬럼으로 승격할지 (나머지는 payload JSONB).
# tenant_id 는 2026-06-24 전면 제거 결정(KL 포털이 격리) — 본 PG 층은 tenant-free.
_COL_KEYS = ("doc_id",)


class PgVectorStore:
    name = "postgres"

    # filter 키 중 전용 컬럼으로 직접 비교할 것(나머지는 payload ->> key).
    _COLUMN_FILTERS = {"doc_id", "chunk_idx"}

    def __init__(self, engine: Any | None = None) -> None:
        # SQLAlchemy 엔진 재사용(풀링·connect_timeout 상속). 지연 연결 — 생성 시 미접속.
        if engine is None:
            from koipa.db.session import engine as _engine  # noqa: PLC0415
            engine = _engine
        self._engine = engine

    # ── 벡터 리터럴 (pgvector 텍스트 입력 '[a,b,...]'::vector — pgvector 파이썬 패키지 불요) ──
    @staticmethod
    def _vec_lit(vec: Sequence[float]) -> str:
        return "[" + ",".join(repr(float(x)) for x in vec) + "]"

    @staticmethod
    def _parse_vec(raw: Any) -> list[float]:
        """pgvector 값 → float 리스트. SELECT 시 텍스트 '[a,b,...]'로 직렬화돼 돌아오므로
        _vec_lit 의 역연산. 이미 시퀀스면 그대로 캐스팅(드라이버/타입어댑터 차이 대비)."""
        if raw is None:
            return []
        if isinstance(raw, (list, tuple)):
            return [float(x) for x in raw]
        s = str(raw).strip()
        if s.startswith("[") and s.endswith("]"):
            s = s[1:-1]
        if not s:
            return []
        try:
            return [float(x) for x in s.split(",") if x.strip()]
        except ValueError:
            return []

    # ── 한국어 어휘 채널 토큰화: 영문/숫자 단어 + 한글 2-gram (pg_bigm 근사) ──
    # NL 재검증 실측: 이 bigram 채널의 ts_rank 가 nori 동급(87% vs 89%), 반면 어절(eojeol)은
    # NL 에서 47%로 붕괴. tsv 는 이 문자열로 생성하고, 질의도 같은 토큰화로 plainto_tsquery 한다.
    _ASCII = re.compile(r"[a-z0-9]+")
    _HAN = re.compile(r"[가-힣]+")

    @classmethod
    def _bigram(cls, text: str) -> str:
        text = (text or "").lower()
        toks = cls._ASCII.findall(text)
        for run in cls._HAN.findall(text):
            if len(run) < 2:
                toks.append(run)
            else:
                toks.extend(run[i:i + 2] for i in range(len(run) - 1))
        return " ".join(toks)

    def _filter_sql(self, filter: dict | None, params: dict, prefix: str = "f") -> str:
        """filter dict → AND 절. 전용 컬럼은 직접, 그 외는 payload ->> 비교."""
        if not filter:
            return ""
        clauses = []
        for i, (k, v) in enumerate(filter.items()):
            if not _FILTER_KEY_RE.fullmatch(str(k)):
                raise ValueError(f"invalid pg vector filter key: {k!r}")
            pk = f"{prefix}{i}"
            if k in self._COLUMN_FILTERS:
                clauses.append(f"{k} = :{pk}")
                params[pk] = v
            else:
                clauses.append(f"payload ->> '{k}' = :{pk}")
                params[pk] = str(v)
        return " AND " + " AND ".join(clauses) if clauses else ""

    def _resolve_collection(self, collection: str) -> str:
        """Resolve blue/green alias names to physical PG collections."""
        from sqlalchemy import text  # noqa: PLC0415

        try:
            with self._engine.connect() as conn:
                target = conn.execute(
                    text("SELECT collection FROM tb_rag_aliases WHERE alias = :a"),
                    {"a": collection},
                ).scalar()
            return str(target or collection)
        except Exception as exc:  # noqa: BLE001
            logger.debug("pg alias resolution skipped for %s: %s", collection, exc)
            return collection

    # ─────────────────────────────────────────────────────────────
    def ensure_collection(self, name: str, dim: int) -> None:
        """행 기반(collection 컬럼) — 테이블은 마이그레이션이 선생성. dim 정합만 검증."""
        if dim != _EMBED_DIM:
            raise ValueError(
                f"PgVectorStore 는 vector({_EMBED_DIM}) 고정인데 dim={dim} 요청 — "
                f"임베딩 모델 차원 불일치(마이그레이션 vector 컬럼 차원과 맞춰야 함)."
            )

    def upsert(
        self,
        collection: str,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        payloads: Sequence[dict] | None = None,
    ) -> int:
        from sqlalchemy import text  # noqa: PLC0415

        payloads = payloads or [{} for _ in ids]
        rows = []
        for _id, vec, pl in zip(ids, vectors, payloads, strict=True):
            pl = dict(pl)
            content = pl.get("text") or pl.get("content") or ""
            rows.append({
                "collection": collection,
                "id": str(_id),
                "doc_id": pl.get("doc_id"),
                "chunk_idx": pl.get("chunk_idx"),
                "content": content,
                "bigram_text": self._bigram(content),   # ts_rank 어휘 채널(분석기 없는 한국어 견고)
                "embedding": self._vec_lit(vec),
                "payload": json.dumps(pl, ensure_ascii=False),
            })
        sql = text(
            """
            INSERT INTO tb_rag_vectors
                (collection, id, doc_id, chunk_idx, content, bigram_text, embedding, payload)
            VALUES
                (:collection, :id, :doc_id, :chunk_idx, :content, :bigram_text,
                 (:embedding)::vector, (:payload)::jsonb)
            ON CONFLICT (collection, id) DO UPDATE SET
                doc_id      = EXCLUDED.doc_id,
                chunk_idx   = EXCLUDED.chunk_idx,
                content     = EXCLUDED.content,
                bigram_text = EXCLUDED.bigram_text,
                embedding   = EXCLUDED.embedding,
                payload     = EXCLUDED.payload
            """
        )
        with self._engine.begin() as conn:
            conn.execute(sql, rows)
        return len(rows)

    def delete(
        self,
        collection: str,
        *,
        ids: Sequence[str] | None = None,
        filter: dict | None = None,
    ) -> int:
        from sqlalchemy import bindparam, text  # noqa: PLC0415

        if not ids and not filter:
            return 0
        params: dict[str, Any] = {"collection": collection}
        where = "collection = :collection"
        if ids:
            params["ids"] = [str(i) for i in ids]
            where += " AND id IN :ids"
        where += self._filter_sql(filter, params)
        sql = text(f"DELETE FROM tb_rag_vectors WHERE {where}")
        if ids:
            sql = sql.bindparams(bindparam("ids", expanding=True))
        with self._engine.begin() as conn:
            res = conn.execute(sql, params)
            return int(res.rowcount or 0)

    def search(
        self,
        collection: str,
        query: Sequence[float],
        top_k: int = 5,
        filter: dict | None = None,
    ) -> list[SearchHit]:
        from sqlalchemy import text  # noqa: PLC0415

        collection = self._resolve_collection(collection)
        params: dict[str, Any] = {"collection": collection, "q": self._vec_lit(query), "k": top_k}
        where = "collection = :collection" + self._filter_sql(filter, params)
        sql = text(
            f"""
            SELECT id, payload, content, 1 - (embedding <=> (:q)::vector) AS score
            FROM tb_rag_vectors
            WHERE {where} AND embedding IS NOT NULL
            ORDER BY embedding <=> (:q)::vector
            LIMIT :k
            """
        )
        with self._engine.connect() as conn:
            return [self._hit(r) for r in conn.execute(sql, params)]

    def search_hybrid(
        self,
        collection: str,
        query_text: str,
        query_vec: Sequence[float],
        top_k: int = 5,
        filter: dict | None = None,
        rrf_window: int = _CAND_N,
        rrf_constant: int = _RRF_K,
        **_: Any,
    ) -> list[SearchHit]:
        """dense(pgvector) + 어휘(ts_rank_cd) 후보를 RRF(k=rrf_constant)로 SQL 내 융합.

        어휘 후보는 tsv @@ plainto_tsquery('simple', q) (pg_bigm GIN 후보 가속 가능) 를
        ts_rank_cd 로 재채점 — raw 유사도 랭킹 회피(붕괴 방지). dense 후보가 비면 어휘만,
        역도 성립(FULL OUTER JOIN).
        """
        from sqlalchemy import text  # noqa: PLC0415

        # OR 의미(BM25 정합): plainto_tsquery 는 모든 토큰 AND라 패러프레이즈서 0매치(크럭스서 실측)
        # → bigram 을 ' | ' 로 묶어 to_tsquery. 토큰은 [가-힣]{2}/[a-z0-9]+ 라 tsquery 특수문자 없음.
        collection = self._resolve_collection(collection)
        q_or = " | ".join(self._bigram(query_text).split()) or "__nomatch__"
        params: dict[str, Any] = {
            "collection": collection, "q": self._vec_lit(query_vec),
            "qt": q_or,
            "cand": rrf_window, "k": rrf_constant, "topk": top_k,
        }
        fwhere = self._filter_sql(filter, params)
        sql = text(
            f"""
            WITH d AS (
                SELECT id, payload, content,
                       row_number() OVER (ORDER BY embedding <=> (:q)::vector) AS rn
                FROM tb_rag_vectors
                WHERE collection = :collection AND embedding IS NOT NULL{fwhere}
                ORDER BY embedding <=> (:q)::vector
                LIMIT :cand
            ),
            l AS (
                SELECT id, payload, content,
                       row_number() OVER (
                           -- ts_rank(plain, norm=1: 1+log(len) 길이정규화) — cover-density(ts_rank_cd)는
                           -- OR-매칭 흩어진 bigram에 근접도 0이라 붕괴(실측 39%); plain+norm1=87%(≈nori). 크럭스 스윕 근거.
                           ORDER BY ts_rank(tsv, to_tsquery('simple', :qt), 1) DESC
                       ) AS rn
                FROM tb_rag_vectors
                WHERE collection = :collection
                  AND tsv @@ to_tsquery('simple', :qt){fwhere}
                LIMIT :cand
            ),
            fused AS (
                SELECT
                    COALESCE(d.id, l.id) AS id,
                    COALESCE(d.payload, l.payload) AS payload,
                    COALESCE(d.content, l.content) AS content,
                    COALESCE(1.0 / (:k + d.rn), 0) + COALESCE(1.0 / (:k + l.rn), 0) AS score
                FROM d FULL OUTER JOIN l ON d.id = l.id
            )
            SELECT id, payload, content, score
            FROM fused
            ORDER BY score DESC
            LIMIT :topk
            """
        )
        with self._engine.connect() as conn:
            self._apply_parallel_hint(conn)
            return [self._hit(r) for r in conn.execute(sql, params)]

    # ── 어휘 후보 순차 스캔의 병렬 워커 (2026-08-16 실측) ──
    @staticmethod
    def _apply_parallel_hint(conn: Any) -> None:
        """검색 트랜잭션에만 병렬 워커 수를 건다.

        어휘 후보 조건 `tsv @@ to_tsquery('simple', bigram1 | bigram2 | ...)` 은 코퍼스의
        99.6%(14,703/14,755)를 매칭한다. 그렇게 넓으면 GIN 인덱스가 순차 스캔보다 비싸서
        플래너가 인덱스를 안 쓴다 - OR 의미를 택한 설계의 결과다(AND 로 하면 패러프레이즈서
        0 매치). 그래서 스캔 자체는 못 없애고, **그 스캔의 병렬 워커 수**가 지연을 지배한다.

            KL 서버 실측 · 코퍼스 14,755 청크 · 쿼리 40건 · 단일 클라이언트
              기본(워커 2)   p50 191.8ms · p95 237.9ms
              워커 4         p50 124.3ms · p95 151.6ms   반환 id·점수 40/40 동일

        PostgreSQL 은 `min_parallel_table_scan_size`(기본 8MB)를 눈금 삼아 테이블 크기에서
        워커 수를 정한다(3배마다 +1: 8MB=1·24MB=2·72MB=3). 이 테이블 힙이 26MB 라 2개에서
        멈춘다 - 그래서 눈금을 0 으로 둬야 `max_parallel_workers_per_gather` 가 실제로 듣는다.
        둘 다 있어야 하고, 둘이면 충분하다(parallel_setup_cost·parallel_tuple_cost 는
        빼도 효과가 유지된다 - scripts/bench_pg_minimal_sets.py 실측).

        `SET LOCAL` 이라 이 트랜잭션에서만 유효하다. 연결은 풀에서 재사용되므로 `SET`(세션)
        으로 걸면 같은 연결의 다른 쿼리까지 영향을 받는다 - 그래서 LOCAL 이다.

        ⚠ 동시 요청이 늘면 이득이 사라진다. 쿼리 하나가 워커 N 개를 잡는데 서버 전체
          `max_parallel_workers` 는 유한하다. CPU 8개 서버 실측에서 동시 4 이상은 처리량이
          포화해 설정과 무관해지고, 워커를 6개로 올리면 꼬리(p95)가 되레 나빠졌다.
          운영 사양·동시 사용자 수가 다르면 `scripts/bench_pg_concurrency.py` 로 다시 잰다.

        ⚠ 실패해도 검색은 진행한다. 이건 속도 조정이지 정확성 조건이 아니다.
        """
        from koipa.config import settings  # noqa: PLC0415

        n = int(getattr(settings, "pg_search_parallel_workers", 0) or 0)
        if n <= 0:
            return
        from sqlalchemy import text  # noqa: PLC0415

        try:
            conn.execute(text("SET LOCAL min_parallel_table_scan_size = 0"))
            conn.execute(text(f"SET LOCAL max_parallel_workers_per_gather = {n}"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("병렬 워커 설정 실패 - 서버 기본값으로 진행합니다: %s", exc)

    def count(self, collection: str) -> int:
        from sqlalchemy import text  # noqa: PLC0415

        collection = self._resolve_collection(collection)
        with self._engine.connect() as conn:
            r = conn.execute(
                text("SELECT count(*) FROM tb_rag_vectors WHERE collection = :c"),
                {"c": collection},
            ).scalar()
        return int(r or 0)

    # ── drift 모니터 표본 (A4) ──
    def sample_vectors(self, *, limit: int = 200, collection: str | None = None) -> list[list[float]]:
        """drift_monitor 용 — 최근 저장 임베딩 표본(created_at DESC).

        [NEW-H1] default 백엔드가 'pg'로 전환된 뒤 이 메서드가 없으면 drift_monitor 의
        _sample_prod_vectors 가 빈 표본을 받아 **실배포에서 drift 점검이 영구 no-op**이
        된다(celery beat drift_tick 이 매 15분 skip). inmemory_store.sample_vectors 와
        동일 시그니처. created_at(마이그레이션 a1b2c3d4e5f6) 으로 최근 N개. collection
        미지정 시 전 컬렉션 합산.

        pgvector embedding 컬럼은 SELECT 시 텍스트 '[a,b,...]' 로 돌아오므로 _parse_vec 로 파싱.
        """
        from sqlalchemy import text  # noqa: PLC0415

        params: dict[str, Any] = {"limit": int(limit)}
        where = "embedding IS NOT NULL"
        if collection:
            where += " AND collection = :collection"
            params["collection"] = collection
        sql = text(
            f"""
            SELECT embedding FROM tb_rag_vectors
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT :limit
            """
        )
        try:
            with self._engine.connect() as conn:
                rows = conn.execute(sql, params).fetchall()
        except Exception as exc:  # noqa: BLE001 — drift 표본은 best-effort(미가용 시 빈 표본)
            logger.debug("pg sample_vectors failed: %s", exc)
            return []
        out: list[list[float]] = []
        for r in rows:
            vec = self._parse_vec(r[0])
            if vec:
                out.append(vec)
        return out

    # ── alias (무중단 재색인 blue/green) — ES swap_alias 대체 ──
    def swap_alias(
        self,
        alias: str,
        new_index: str,
        old_index: str | None = None,
        *,
        delete_old: bool = True,
    ) -> None:
        from sqlalchemy import text  # noqa: PLC0415

        with self._engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO tb_rag_aliases (alias, collection)
                    VALUES (:alias, :coll)
                    ON CONFLICT (alias) DO UPDATE SET collection = EXCLUDED.collection, updated_at = now()
                    """
                ),
                {"alias": alias, "coll": new_index},
            )
            if delete_old and old_index and old_index != new_index:
                conn.execute(
                    text("DELETE FROM tb_rag_vectors WHERE collection = :c"),
                    {"c": old_index},
                )

    def current_alias_target(self, alias: str) -> str | None:
        """alias 가 가리키는 현재 collection. (RagIndexer 의 _current_alias_target 대체 훅)."""
        from sqlalchemy import text  # noqa: PLC0415

        with self._engine.connect() as conn:
            return conn.execute(
                text("SELECT collection FROM tb_rag_aliases WHERE alias = :a"),
                {"a": alias},
            ).scalar()

    # ── helpers ──
    @staticmethod
    def _hit(row: Any) -> SearchHit:
        m = row._mapping
        payload = m["payload"] or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:  # noqa: BLE001
                payload = {}
        payload = dict(payload)
        # 표시·reranker 용 text 보장 (es_store payload['text'] 와 정합).
        if "text" not in payload and m.get("content"):
            payload["text"] = m["content"]
        return SearchHit(id=str(m["id"]), score=float(m["score"] or 0.0), payload=payload)
