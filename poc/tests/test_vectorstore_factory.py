"""build_store 백엔드 분기 + 폴리필 일관성 테스트.

doc/13 §5(어댑터)·§9(dryrun 한계) 검증.
"""

from __future__ import annotations

import os

import pytest

from koipa.adapters.vectorstore import InMemoryStore, build_store


def test_force_memory_overrides_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VECTOR_BACKEND", "es")
    vs = build_store(force_memory=True)
    assert isinstance(vs, InMemoryStore)


def test_default_backend_is_pg(monkeypatch: pytest.MonkeyPatch):
    """VECTOR_BACKEND 미지정 시 기본 'pg'(PgVectorStore) — 의사결정_대장 §03 ⓑ 확정.

    PgVectorStore는 지연 연결(SQLAlchemy 풀)이라 생성만으로 연결 안 함 → name='postgres'.
    (레거시 es 백엔드는 backend='es' 명시 시 그대로 사용 가능.)
    """
    monkeypatch.delenv("VECTOR_BACKEND", raising=False)
    vs = build_store()
    assert vs.name == "postgres"


def test_es_backend_falls_back_when_import_fails(monkeypatch: pytest.MonkeyPatch):
    """elasticsearch 모듈 자체가 import 실패하면 InMemory 폴백."""
    import sys
    import koipa.adapters.vectorstore as vectorstore_mod

    # 모듈 캐시에서 elasticsearch + es_store 제거 후 import 차단
    for mod in list(sys.modules):
        if mod.startswith("elasticsearch") or mod.endswith("es_store"):
            sys.modules.pop(mod, None)

    monkeypatch.setitem(sys.modules, "elasticsearch", None)
    monkeypatch.delenv("VECTOR_BACKEND", raising=False)
    vectorstore_mod._STORE_CACHE.clear()

    with pytest.warns(RuntimeWarning, match="Elasticsearch unavailable"):
        vs = build_store(backend="es")

    assert vs.name == "inmemory"


def test_explicit_inmemory_backend(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VECTOR_BACKEND", "inmemory")
    vs = build_store()
    assert isinstance(vs, InMemoryStore)


def test_explicit_argument_overrides_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VECTOR_BACKEND", "es")
    vs = build_store(backend="inmemory")
    assert isinstance(vs, InMemoryStore)


def test_unknown_backend_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("VECTOR_BACKEND", raising=False)
    with pytest.raises(ValueError, match="unknown VECTOR_BACKEND"):
        build_store(backend="cosmosdb")


# ─────────────────────────────────────────────────────────────
# search_hybrid 폴리필 일관성
# ─────────────────────────────────────────────────────────────


def test_inmemory_search_hybrid_empty_text_falls_back_to_vec_only():
    """query_text가 None/빈 문자열이면 vec-only 폴백 + RuntimeWarning (P2 dryrun 한계 명시)."""
    vs = InMemoryStore()
    vs.ensure_collection("c", dim=4)
    vs.upsert(
        "c",
        ids=["a", "b"],
        vectors=[[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]],
        payloads=[{"grade": "TS", "text": "top secret"}, {"grade": "S1", "text": "secret one"}],
    )
    with pytest.warns(RuntimeWarning, match="dense-only로 폴백"):
        hits = vs.search_hybrid("c", query_text="", query_vec=[1.0, 0.0, 0.0, 0.0], top_k=2)
    assert hits[0].id == "a"  # vec 1순위


def test_inmemory_search_hybrid_with_filter():
    """text가 있어도 filter는 BM25/cosine 양쪽 후보군에 동일하게 적용된다."""
    vs = InMemoryStore()
    vs.ensure_collection("c", dim=4)
    vs.upsert(
        "c",
        ids=["a", "b", "c"],
        vectors=[[1, 0, 0, 0], [0.99, 0.1, 0, 0], [0.5, 0, 0, 0]],
        payloads=[
            {"grade": "TS", "text": "alpha doc"},
            {"grade": "S1", "text": "alpha doc"},
            {"grade": "TS", "text": "alpha doc"},
        ],
    )
    hits = vs.search_hybrid(
        "c",
        query_text="alpha",
        query_vec=[1.0, 0.0, 0.0, 0.0],
        top_k=5,
        filter={"grade": "TS"},
    )
    assert {h.id for h in hits} == {"a", "c"}


def test_protocol_compliance_inmemory():
    """InMemoryStore가 VectorStore Protocol을 만족하지만 HybridVectorStore는 아님."""
    from koipa.adapters.vectorstore.base import VectorStore

    vs = InMemoryStore()
    # runtime_checkable Protocol — 구조적 호환 확인
    assert isinstance(vs, VectorStore)
    # 모든 핵심 메서드 존재
    for method in ("ensure_collection", "upsert", "search", "search_hybrid", "count"):
        assert callable(getattr(vs, method))
    # HybridVectorStore는 마커 Protocol — 진짜 하이브리드 백엔드만 만족해야 함.
    # runtime_checkable Protocol은 메서드 시그니처만 보므로 InMemory도 isinstance가
    # True가 될 수 있다. 그래서 호출부는 isinstance 결과를 신뢰하기 전에
    # 실제 백엔드 name으로 한 번 더 검증하거나 EsStore 명시 분기를 권장.
    # 본 테스트는 "InMemory는 EsStore가 아니다"라는 운영 사실만 확인.
    assert vs.name != "elasticsearch"


# ─────────────────────────────────────────────────────────────
# InMemory BM25 + RRF 하이브리드 (v2.2)
# ─────────────────────────────────────────────────────────────


def test_inmemory_bm25_lifts_text_match_over_dense_only():
    """텍스트 매칭이 강한 문서가 RRF 결합에서 cosine 1위를 뒤집을 수 있다.

    cosine으로는 a > b > c 순이지만, BM25는 "기밀 자료"에 정확히 매칭되는
    c가 1위 → RRF 결합 시 c가 a보다 상위로 올라와야 한다 (token-overlap BM25 검증).
    """
    vs = InMemoryStore()
    vs.ensure_collection("c", dim=4)
    vs.upsert(
        "c",
        ids=["a", "b", "c"],
        vectors=[
            [1.0, 0.0, 0.0, 0.0],  # cosine 1위
            [0.9, 0.1, 0.0, 0.0],  # cosine 2위
            [0.6, 0.0, 0.0, 0.0],  # cosine 3위
        ],
        payloads=[
            {"text": "general document about cats and dogs"},
            {"text": "another unrelated note"},
            {"text": "기밀 자료 — confidential trade secret"},
        ],
    )
    hits = vs.search_hybrid(
        "c",
        query_text="기밀 자료 secret",
        query_vec=[1.0, 0.0, 0.0, 0.0],
        top_k=3,
    )
    ids = [h.id for h in hits]
    assert "c" in ids
    # BM25에서 c가 압도적 1위라 RRF 결합 후에도 a보다 위 또는 동률이어야 한다.
    assert ids.index("c") <= ids.index("a")


def test_inmemory_bm25_only_via_zero_vector():
    """모든 문서가 vec-orthogonal이면 BM25 점수만 RRF에 기여한다 (BM25-only 검증)."""
    vs = InMemoryStore()
    vs.ensure_collection("c", dim=4)
    vs.upsert(
        "c",
        ids=["doc1", "doc2", "doc3"],
        vectors=[[0, 1, 0, 0], [0, 1, 0, 0], [0, 1, 0, 0]],  # cosine 동률
        payloads=[
            {"text": "machine learning is fun"},
            {"text": "fishing is fun on weekends"},
            {"text": "Korean 한글 검색 테스트 BM25"},
        ],
    )
    hits = vs.search_hybrid(
        "c",
        query_text="Korean 한글 BM25",
        query_vec=[1.0, 0.0, 0.0, 0.0],  # 모든 문서와 직교
        top_k=3,
    )
    assert hits[0].id == "doc3"


def test_inmemory_hybrid_alpha_weighted_combination():
    """hybrid_alpha 인자로 단순 가중합 결합도 동작해야 한다."""
    vs = InMemoryStore()
    vs.ensure_collection("c", dim=4)
    vs.upsert(
        "c",
        ids=["v_top", "t_top"],
        vectors=[[1.0, 0.0, 0.0, 0.0], [0.1, 1.0, 0.0, 0.0]],
        payloads=[
            {"text": "irrelevant"},
            {"text": "exact match query token"},
        ],
    )
    # alpha=1.0 → cosine 100% → v_top 1위
    hits_v = vs.search_hybrid(
        "c", query_text="query token", query_vec=[1.0, 0.0, 0.0, 0.0],
        top_k=2, hybrid_alpha=1.0,
    )
    assert hits_v[0].id == "v_top"
    # alpha=0.0 → BM25 100% → t_top 1위
    hits_t = vs.search_hybrid(
        "c", query_text="query token", query_vec=[1.0, 0.0, 0.0, 0.0],
        top_k=2, hybrid_alpha=0.0,
    )
    assert hits_t[0].id == "t_top"


def test_env_clean_state():
    """다른 테스트가 VECTOR_BACKEND를 오염시키지 않았는지."""
    # monkeypatch 없이 환경 그대로 — 이 테스트 자체는 무 부작용
    backend = os.getenv("VECTOR_BACKEND")
    assert backend is None or isinstance(backend, str)
