"""PgVectorStore 라이브 통합 — 프로덕션 기본 백엔드(pg)의 하이브리드 SQL을 실 Postgres로 검증.

배경(감사 P1): test_pg_store.py 는 순수 헬퍼(_vec_lit/_bigram/_filter_sql)만 돌고, 라이브 검증은
scripts/revalidate_pg_lexical.py(수동)로 미뤄져 있었다. 유일한 fullstack 인덱서 테스트는 build_store(
backend='es')로 **레거시 ES**를 쳐서, 프로덕션 기본 pg 경로(GENERATED tsv 컬럼·to_tsquery·RRF FULL
OUTER JOIN·embedding<=>vector·_parse_vec 왕복)가 CI에서 한 번도 실행되지 않았다. 본 테스트가 그
경로를 CI postgres(pgvector+alembic head)에서 실제로 돌린다.

fullstack 마커 — test-lite 는 제외, CI 의 make test-fullstack 스텝(및 로컬 PG)에서 실행.
결정론: HashEmbedding(모델 다운로드 불요, 동일 텍스트→동일 벡터). 정확 텍스트 질의는 코사인 1.0 이라
등급 판정이 임베딩 의미품질에 의존하지 않는다.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from koipa.adapters.embedding import HashEmbedding
from koipa.db import engine


def _pg_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False
    except Exception:  # noqa: BLE001
        return False


_DOCS = [
    ("pgl-d1", "영업비밀 등급 분류 기준과 VRF compressor 사양 문서"),
    ("pgl-d2", "공개 판례 요약과 일반 공시 정보 안내 자료"),
    ("pgl-d3", "반도체 공정 레시피와 수율 데이터 상세 명세"),
]
_IDS = [d[0] for d in _DOCS]
_TEXTS = [d[1] for d in _DOCS]
_COLLECTION = "pytest-pg-live-hybrid"


@pytest.mark.fullstack
class TestPgLiveHybrid:
    def _store(self):
        from koipa.adapters.vectorstore import build_store  # noqa: PLC0415

        return build_store(backend="pg")

    def setup_method(self):
        if not _pg_ok():
            pytest.skip("Postgres not reachable")
        # 이전 실패 run 잔재 정리(멱등).
        self._store().delete(_COLLECTION, ids=_IDS)

    def teardown_method(self):
        if _pg_ok():
            self._store().delete(_COLLECTION, ids=_IDS)

    def _seed(self, store):
        emb = HashEmbedding(dim=1024)
        vecs = emb.embed(_TEXTS).vectors
        payloads = [{"doc_id": _IDS[i], "text": _TEXTS[i], "chunk_idx": 0} for i in range(3)]
        store.ensure_collection(_COLLECTION, 1024)
        assert store.upsert(_COLLECTION, _IDS, vecs, payloads) == 3
        return emb

    def test_build_store_returns_pg(self):
        store = self._store()
        assert store.name == "postgres"

    def test_upsert_count_and_dense_search(self):
        store = self._store()
        emb = self._seed(store)
        assert store.count(_COLLECTION) == 3

        # dense: 정확 텍스트 질의 → 동일 임베딩(코사인 1.0) → 자기 문서 최상위(embedding<=>vector).
        qv = emb.embed([_TEXTS[0]]).vectors[0]
        dense = store.search(_COLLECTION, qv, top_k=3)
        assert dense and dense[0].id == "pgl-d1"
        assert dense[0].score > 0.99
        assert dense[0].payload.get("text")  # content → payload['text'] 하이드레이션

    def test_hybrid_rrf_and_tsv_channel(self):
        store = self._store()
        emb = self._seed(store)

        # 자기 텍스트 질의 → dense+어휘 모두 자기 문서 → RRF 최상위.
        qv0 = emb.embed([_TEXTS[0]]).vectors[0]
        hybrid = store.search_hybrid(_COLLECTION, _TEXTS[0], qv0, top_k=3)
        assert hybrid and hybrid[0].id == "pgl-d1"
        assert hybrid[0].score > 0

        # 채널 융합 증명: dense 벡터=d1, 어휘 질의문=d2 본문 → FULL OUTER JOIN 이 양 채널 후보를 융합.
        # (tsv GENERATED 컬럼 + to_tsquery('simple', bigram) 어휘 채널이 실제로 후보를 기여하는지.)
        mixed = store.search_hybrid(_COLLECTION, _TEXTS[1], qv0, top_k=3)
        found = {h.id for h in mixed}
        assert "pgl-d1" in found, "dense 채널(embedding<=>vector) 기여 누락"
        assert "pgl-d2" in found, "어휘 채널(tsv @@ to_tsquery) 기여 누락 — RRF 융합 실패"

    def test_column_filter_and_parse_vec_roundtrip(self):
        store = self._store()
        emb = self._seed(store)

        # doc_id 전용 컬럼 필터(payload JSONB 아님).
        qv = emb.embed([_TEXTS[0]]).vectors[0]
        filtered = store.search(_COLLECTION, qv, top_k=3, filter={"doc_id": "pgl-d2"})
        assert [h.id for h in filtered] == ["pgl-d2"]

        # _parse_vec 왕복: pgvector embedding 은 SELECT 시 텍스트 '[a,b,...]' 로 돌아온다.
        sv = store.sample_vectors(limit=10, collection=_COLLECTION)
        assert sv and all(len(v) == 1024 for v in sv)
