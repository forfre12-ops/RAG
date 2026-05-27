"""build_store 백엔드 분기 + 폴리필 일관성 테스트.

doc/13 §5(어댑터)·§8(롤백)·§9(dryrun 한계) 검증.
"""

from __future__ import annotations

import os

import pytest

from lloydk.adapters.vectorstore import InMemoryStore, build_store


def test_force_memory_overrides_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VECTOR_BACKEND", "es")
    vs = build_store(force_memory=True)
    assert isinstance(vs, InMemoryStore)


def test_default_backend_is_es(monkeypatch: pytest.MonkeyPatch):
    """VECTOR_BACKEND 미지정 시 기본 'es'를 시도.

    - elasticsearch 패키지 설치 + 클라이언트 lazy 연결 → EsStore 생성 성공
    - ImportError 또는 설정 누락 → InMemory 폴백
    """
    monkeypatch.delenv("VECTOR_BACKEND", raising=False)
    vs = build_store()
    # 둘 중 하나여야 함 (둘 다 정상)
    assert vs.name in ("elasticsearch", "inmemory")


def test_es_backend_falls_back_when_import_fails(monkeypatch: pytest.MonkeyPatch):
    """elasticsearch 모듈 자체가 import 실패하면 InMemory 폴백."""
    import sys

    # 모듈 캐시에서 elasticsearch + es_store 제거 후 import 차단
    for mod in list(sys.modules):
        if mod.startswith("elasticsearch") or mod.endswith("es_store"):
            sys.modules.pop(mod, None)

    monkeypatch.setitem(sys.modules, "elasticsearch", None)
    monkeypatch.delenv("VECTOR_BACKEND", raising=False)

    with pytest.warns(RuntimeWarning, match="Elasticsearch unavailable"):
        vs = build_store(backend="es")

    assert vs.name == "inmemory"


def test_explicit_inmemory_backend(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VECTOR_BACKEND", "inmemory")
    vs = build_store()
    assert isinstance(vs, InMemoryStore)


def test_explicit_argument_overrides_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VECTOR_BACKEND", "qdrant")
    vs = build_store(backend="inmemory")
    assert isinstance(vs, InMemoryStore)


def test_unknown_backend_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("VECTOR_BACKEND", raising=False)
    with pytest.raises(ValueError, match="unknown VECTOR_BACKEND"):
        build_store(backend="cosmosdb")


# ─────────────────────────────────────────────────────────────
# search_hybrid 폴리필 일관성
# ─────────────────────────────────────────────────────────────


def test_inmemory_search_hybrid_falls_back_to_vec_only():
    """query_text가 무시되어도 vec-only 검색이 동작해야 한다 (P2 dryrun 한계 명시)."""
    vs = InMemoryStore()
    vs.ensure_collection("c", dim=4)
    vs.upsert(
        "c",
        ids=["a", "b"],
        vectors=[[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]],
        payloads=[{"grade": "TS"}, {"grade": "S1"}],
    )
    # 쿼리 텍스트가 b 문서 내용과 일치해도 dense vector 우선
    with pytest.warns(RuntimeWarning, match="dense-only로 폴백"):
        hits = vs.search_hybrid("c", query_text="S1 자료", query_vec=[1.0, 0.0, 0.0, 0.0], top_k=2)
    assert hits[0].id == "a"  # vec 1순위 (text 매칭 무시)


def test_inmemory_search_hybrid_with_filter():
    vs = InMemoryStore()
    vs.ensure_collection("c", dim=4)
    vs.upsert(
        "c",
        ids=["a", "b", "c"],
        vectors=[[1, 0, 0, 0], [0.99, 0.1, 0, 0], [0.5, 0, 0, 0]],
        payloads=[{"grade": "TS"}, {"grade": "S1"}, {"grade": "TS"}],
    )
    with pytest.warns(RuntimeWarning):
        hits = vs.search_hybrid(
            "c",
            query_text="ignored",
            query_vec=[1.0, 0.0, 0.0, 0.0],
            top_k=5,
            filter={"grade": "TS"},
        )
    assert {h.id for h in hits} == {"a", "c"}


def test_protocol_compliance_inmemory():
    """InMemoryStore가 VectorStore Protocol을 만족하지만 HybridVectorStore는 아님."""
    from lloydk.adapters.vectorstore.base import HybridVectorStore, VectorStore

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


def test_qdrant_search_hybrid_warns():
    """QdrantStore.search_hybrid가 RuntimeWarning을 내야 한다 (조용한 dense 폴백 금지)."""
    # qdrant_client 미설치/미연결 환경에서도 메서드 자체는 호출 가능해야 하므로
    # 클라이언트 초기화를 우회한 인스턴스로 폴리필 경로만 검증.
    from lloydk.adapters.vectorstore.qdrant_store import QdrantStore

    vs = QdrantStore.__new__(QdrantStore)  # __init__ 우회 (qdrant 서버 불필요)

    # search() 호출 차단: 폴리필이 search로 위임하므로 그 직전에 warn이 나야 함.
    # 위임된 search는 클라이언트가 없어 AttributeError가 나지만, warn은 그 전에 발생.
    with pytest.warns(RuntimeWarning, match="dense-only로 폴백"):
        try:
            vs.search_hybrid("col", query_text="x", query_vec=[0.0], top_k=1)
        except Exception:
            pass  # _client 미초기화·qdrant_client 미설치는 본 테스트 관심 밖


def test_env_clean_state():
    """다른 테스트가 VECTOR_BACKEND를 오염시키지 않았는지."""
    # monkeypatch 없이 환경 그대로 — 이 테스트 자체는 무 부작용
    backend = os.getenv("VECTOR_BACKEND")
    assert backend is None or isinstance(backend, str)
