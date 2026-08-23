"""A6: retrieval facade — query expansion + RRF 결합 검증.

InMemoryStore + 결정론적 encode로 RRF 동작·폴백·max_queries clamp 검증.
"""

from __future__ import annotations

import math

import pytest

from koipa.adapters.vectorstore.inmemory_store import InMemoryStore
from koipa.services.retrieval import _rrf_combine, expand_then_search
from koipa.adapters.vectorstore.base import SearchHit


@pytest.fixture
def store() -> InMemoryStore:
    s = InMemoryStore()
    s.ensure_collection("docs", dim=4)
    # 4개 문서, 각자 직교 단위벡터에 가까움
    s.upsert(
        "docs",
        ids=["d1", "d2", "d3", "d4"],
        vectors=[
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        payloads=[
            {"text": "M&A 인수합병 계약"},
            {"text": "특허 출원 보고서"},
            {"text": "이사회 의사록"},
            {"text": "기타 일반 문서"},
        ],
    )
    return s


def _encode_keyword(text: str):
    """단순 키워드 → 4차원 벡터. 결정론적."""
    text_l = text.lower()
    return [
        1.0 if any(k in text_l for k in ("m&a", "인수", "merger", "acquisition")) else 0.0,
        1.0 if "특허" in text or "patent" in text_l else 0.0,
        1.0 if "이사회" in text or "board" in text_l else 0.0,
        0.0,
    ]


# ---------------------------------------------------------------------------
# _rrf_combine
# ---------------------------------------------------------------------------


def test_rrf_combine_accumulates_for_repeated_id():
    set_a = [SearchHit(id="x", score=1.0, payload={})]
    set_b = [SearchHit(id="x", score=0.5, payload={}), SearchHit(id="y", score=0.3, payload={})]
    fused = _rrf_combine([set_a, set_b])
    by_id = {h.id: h.score for h in fused}
    # x는 두 세트 모두 rank 1 → 2 * 1/(60+1) = 0.0327
    assert math.isclose(by_id["x"], 2 * (1.0 / 61.0), rel_tol=1e-6)
    # y는 set_b rank 2 → 1/(60+2)
    assert math.isclose(by_id["y"], 1.0 / 62.0, rel_tol=1e-6)
    # 정렬: x가 위
    assert fused[0].id == "x"


def test_rrf_combine_empty_returns_empty():
    assert _rrf_combine([]) == []
    assert _rrf_combine([[], []]) == []


# ---------------------------------------------------------------------------
# expand_then_search — rule expansion
# ---------------------------------------------------------------------------


def test_expand_then_search_rule_returns_hits(store):
    hits = expand_then_search(
        store=store, collection="docs",
        query_text="M&A 인수",
        encode=_encode_keyword,
        method="rule",
        top_k=3,
    )
    assert hits, "RRF 결합 후 결과가 비어선 안 됨"
    assert hits[0].id == "d1"  # M&A 매칭 문서가 1위


def test_expand_then_search_empty_query_returns_empty(store):
    hits = expand_then_search(
        store=store, collection="docs",
        query_text="",
        encode=_encode_keyword,
    )
    assert hits == []


def test_expand_then_search_falls_back_when_hybrid_unavailable(store):
    """search_hybrid가 예외 → dense search 폴백."""
    original = store.search_hybrid

    def broken_hybrid(*args, **kwargs):
        raise RuntimeError("hybrid not available")

    store.search_hybrid = broken_hybrid  # type: ignore[method-assign]
    try:
        hits = expand_then_search(
            store=store, collection="docs",
            query_text="M&A 인수",
            encode=_encode_keyword,
            method="rule",
            top_k=3,
        )
        assert hits
        assert hits[0].id == "d1"
    finally:
        store.search_hybrid = original  # type: ignore[method-assign]


def test_expand_then_search_clamps_max_queries(store):
    """max_queries로 폭주 차단 — 1개로 제한해도 결과 정상."""
    hits = expand_then_search(
        store=store, collection="docs",
        query_text="M&A 인수",
        encode=_encode_keyword,
        method="rule",
        top_k=2,
        max_queries=1,
    )
    assert len(hits) <= 2


def test_expand_then_search_encode_failure_skips_query(store):
    calls = {"n": 0}

    def flaky_encode(text: str):
        calls["n"] += 1
        if "M&A" in text and calls["n"] > 1:
            # 두 번째 호출부터 실패 — 첫 결과만 살아남음
            raise RuntimeError("encoder down")
        return _encode_keyword(text)

    hits = expand_then_search(
        store=store, collection="docs",
        query_text="M&A 인수",
        encode=flaky_encode,
        method="rule",
        top_k=3,
    )
    # 적어도 첫 쿼리 결과는 들어와야 함 (전체가 비어서는 안 됨)
    assert hits
