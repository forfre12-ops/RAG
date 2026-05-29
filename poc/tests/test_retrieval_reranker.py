"""표적 4 — retrieval facade의 reranker wiring 검증.

NoopReranker로 결정론적 회귀 + 가짜 cross-encoder reranker로 점수 역전 확인 +
payload에 text 없는 hit 보강 + reranker 실패 시 RRF 순서 폴백 + oversample 동작.
"""

from __future__ import annotations

from typing import Sequence

import pytest

from lloydk.adapters.reranker.base import RerankResult
from lloydk.adapters.reranker.noop_reranker import NoopReranker
from lloydk.adapters.vectorstore.base import SearchHit
from lloydk.adapters.vectorstore.inmemory_store import InMemoryStore
from lloydk.services.retrieval import _rerank_hits, expand_then_search


def _encode_keyword(text: str):
    text_l = text.lower()
    return [
        1.0 if any(k in text_l for k in ("m&a", "인수", "merger", "acquisition")) else 0.0,
        1.0 if "특허" in text or "patent" in text_l else 0.0,
        1.0 if "이사회" in text or "board" in text_l else 0.0,
        0.0,
    ]


@pytest.fixture
def store() -> InMemoryStore:
    s = InMemoryStore()
    s.ensure_collection("docs", dim=4)
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


class _ReverseReranker:
    """가짜 cross-encoder — 입력 순서를 뒤집어 반환. 점수는 (1 - i/N)."""
    name = "reverse"

    def rerank(self, query: str, candidates: Sequence[str], *, top_k: int | None = None):
        n = len(candidates)
        reversed_pairs = list(enumerate(candidates))[::-1]
        out = [
            RerankResult(index=i, score=1.0 - (rank / max(n, 1)), document=doc)
            for rank, (i, doc) in enumerate(reversed_pairs)
        ]
        if top_k is not None:
            out = out[:top_k]
        return out


class _BrokenReranker:
    name = "broken"

    def rerank(self, query, candidates, *, top_k=None):
        raise RuntimeError("model OOM")


# ---------------------------------------------------------------------------
# _rerank_hits 단위
# ---------------------------------------------------------------------------


def test_rerank_hits_noop_preserves_input_order():
    hits = [
        SearchHit(id="a", score=0.5, payload={"text": "alpha"}),
        SearchHit(id="b", score=0.3, payload={"text": "beta"}),
        SearchHit(id="c", score=0.1, payload={"text": "gamma"}),
    ]
    out = _rerank_hits(query_text="x", hits=hits, reranker=NoopReranker(), top_k=3)
    assert [h.id for h in out] == ["a", "b", "c"]


def test_rerank_hits_reverse_reorders():
    hits = [
        SearchHit(id="a", score=0.5, payload={"text": "alpha"}),
        SearchHit(id="b", score=0.3, payload={"text": "beta"}),
        SearchHit(id="c", score=0.1, payload={"text": "gamma"}),
    ]
    out = _rerank_hits(query_text="x", hits=hits, reranker=_ReverseReranker(), top_k=3)
    assert [h.id for h in out] == ["c", "b", "a"]
    # 점수도 reranker 점수로 덮어쓰기
    assert out[0].score >= out[-1].score


def test_rerank_hits_top_k_clamps():
    hits = [SearchHit(id=str(i), score=0.5, payload={"text": f"t{i}"}) for i in range(10)]
    out = _rerank_hits(query_text="x", hits=hits, reranker=NoopReranker(), top_k=3)
    assert len(out) == 3


def test_rerank_hits_no_text_hits_fallback_to_rrf_order():
    """모든 hit이 payload['text'] 없으면 RRF 순서대로 top_k 슬라이스."""
    hits = [SearchHit(id=str(i), score=1.0 - i * 0.1, payload={}) for i in range(5)]
    out = _rerank_hits(query_text="x", hits=hits, reranker=NoopReranker(), top_k=3)
    assert [h.id for h in out] == ["0", "1", "2"]


def test_rerank_hits_partial_text_appends_no_text_at_tail():
    hits = [
        SearchHit(id="a", score=0.9, payload={"text": "alpha"}),
        SearchHit(id="b", score=0.8, payload={}),  # text 없음
        SearchHit(id="c", score=0.7, payload={"text": "gamma"}),
    ]
    out = _rerank_hits(query_text="x", hits=hits, reranker=NoopReranker(), top_k=3)
    # 텍스트 있는 hit이 먼저, 그 다음 텍스트 없는 hit 보강
    assert out[0].id in {"a", "c"}
    assert "b" in {h.id for h in out}


def test_rerank_hits_broken_reranker_falls_back_to_rrf_order():
    hits = [
        SearchHit(id="a", score=0.9, payload={"text": "alpha"}),
        SearchHit(id="b", score=0.5, payload={"text": "beta"}),
    ]
    out = _rerank_hits(query_text="x", hits=hits, reranker=_BrokenReranker(), top_k=2)
    assert [h.id for h in out] == ["a", "b"]


def test_rerank_hits_empty_returns_empty():
    assert _rerank_hits(query_text="x", hits=[], reranker=NoopReranker(), top_k=5) == []


# ---------------------------------------------------------------------------
# expand_then_search 통합 — reranker 옵션
# ---------------------------------------------------------------------------


def test_expand_then_search_with_reranker_reverses_order(store):
    """ReverseReranker로 RRF 결과 순서 역전 확인."""
    hits = expand_then_search(
        store=store, collection="docs",
        query_text="M&A 인수",
        encode=_encode_keyword,
        method="rule",
        top_k=4,
        use_reranker=True,
        reranker=_ReverseReranker(),
    )
    # reverse면 RRF 1위(d1)가 마지막으로 밀림
    assert hits, "결과가 비어선 안 됨"
    ids = [h.id for h in hits]
    assert "d1" in ids
    # 1위 hit은 RRF 1위가 아니어야 — 역순이라
    assert ids[0] != "d1" or len(ids) <= 1


def test_expand_then_search_use_reranker_false_preserves_rrf(store):
    """use_reranker=False면 기존 RRF 순서 보존(회귀 가드)."""
    hits = expand_then_search(
        store=store, collection="docs",
        query_text="M&A 인수",
        encode=_encode_keyword,
        method="rule",
        top_k=3,
        use_reranker=False,
    )
    assert hits
    # M&A 매칭 문서 d1이 1위
    assert hits[0].id == "d1"


def test_expand_then_search_oversample_factor_pulls_more_candidates(store):
    """oversample_factor가 1차 search top_k에 곱해지는지 — first_k가 top_k×factor."""
    calls: list[int] = []

    original_search_hybrid = store.search_hybrid

    def spy_search_hybrid(*, collection, query_text, query_vec, top_k, filter=None, **kwargs):
        calls.append(top_k)
        return original_search_hybrid(
            collection=collection, query_text=query_text, query_vec=query_vec,
            top_k=top_k, filter=filter, **kwargs,
        )

    store.search_hybrid = spy_search_hybrid  # type: ignore[method-assign]
    try:
        expand_then_search(
            store=store, collection="docs",
            query_text="M&A 인수",
            encode=_encode_keyword,
            method="rule",
            top_k=2,
            use_reranker=True,
            reranker=NoopReranker(),
            oversample_factor=3,
        )
    finally:
        store.search_hybrid = original_search_hybrid  # type: ignore[method-assign]

    assert calls
    # 모든 1차 호출에서 top_k=2×3=6
    assert all(k == 6 for k in calls)


def test_expand_then_search_default_use_reranker_noop_safe(store):
    """기본값(use_reranker=True + settings.reranker_provider=noop)이 안전한지.

    settings 기본 reranker_provider='noop' — BGE 미설치 환경에서도 NoopReranker로 동작.
    """
    hits = expand_then_search(
        store=store, collection="docs",
        query_text="M&A 인수",
        encode=_encode_keyword,
        method="rule",
        top_k=3,
    )
    assert hits
    assert hits[0].id == "d1"  # noop은 RRF 순서 유지
