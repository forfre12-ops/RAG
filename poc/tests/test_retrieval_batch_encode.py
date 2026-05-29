"""§1: retrieval.expand_then_search의 encode_batch 우선 사용 검증.

확장 쿼리 N개를 1회 forward로 인코딩 — KURE p50 629ms × 4쿼리를 단축.
encode_batch 미주입·길이 불일치·예외 시 단건 encode로 폴백.
"""

from __future__ import annotations

import pytest

from lloydk.adapters.vectorstore.base import SearchHit


class _FakeStore:
    """search_hybrid만 카운트하는 미니멀 store."""
    name = "fake"

    def __init__(self):
        self.search_hybrid_calls: list[tuple[str, list]] = []

    def ensure_collection(self, name: str, dim: int) -> None:
        pass

    def search_hybrid(self, *, collection, query_text, query_vec, top_k, filter=None):
        self.search_hybrid_calls.append((query_text, list(query_vec)))
        return [SearchHit(id=f"hit-{query_text}", score=0.9, payload={"text": "x"})]

    def search(self, *, collection, query, top_k, filter=None):
        return []


def test_encode_batch_invoked_once_for_all_expanded_queries() -> None:
    """encode_batch 주입 시 확장 쿼리 N개에 대해 1회만 호출. encode는 0회."""
    from lloydk.services.retrieval import expand_then_search

    encode_calls: list[str] = []
    batch_calls: list[list[str]] = []

    def _encode(t: str):
        encode_calls.append(t)
        return [1.0, 0.0]

    def _encode_batch(texts):
        batch_calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]

    store = _FakeStore()
    expand_then_search(
        store=store,
        collection="docs",
        query_text="M&A 인수합병 계약",
        encode=_encode,
        encode_batch=_encode_batch,
        method="rule",
        top_k=3,
        use_reranker=False,
    )

    assert len(batch_calls) == 1, "encode_batch는 1회 호출되어야 함"
    assert len(encode_calls) == 0, "encode_batch 성공 시 단건 encode는 호출 안 됨"
    # search_hybrid는 확장 쿼리 수만큼 호출
    assert len(store.search_hybrid_calls) >= 1


def test_falls_back_to_single_encode_when_no_batch() -> None:
    """encode_batch 미주입 시 단건 encode 경로 그대로."""
    from lloydk.services.retrieval import expand_then_search

    encode_calls: list[str] = []

    def _encode(t: str):
        encode_calls.append(t)
        return [1.0, 0.0]

    store = _FakeStore()
    expand_then_search(
        store=store,
        collection="docs",
        query_text="M&A 인수합병 계약",
        encode=_encode,
        method="rule",
        top_k=3,
        use_reranker=False,
    )

    assert len(encode_calls) >= 1, "encode_batch 미주입 시 단건 encode가 호출되어야 함"


def test_falls_back_to_single_encode_on_batch_exception() -> None:
    """encode_batch가 예외 던지면 단건 encode로 안전 폴백."""
    from lloydk.services.retrieval import expand_then_search

    encode_calls: list[str] = []

    def _encode(t: str):
        encode_calls.append(t)
        return [1.0, 0.0]

    def _broken_batch(texts):
        raise RuntimeError("batch backend down")

    store = _FakeStore()
    expand_then_search(
        store=store,
        collection="docs",
        query_text="M&A 인수합병 계약",
        encode=_encode,
        encode_batch=_broken_batch,
        method="rule",
        top_k=3,
        use_reranker=False,
    )

    assert len(encode_calls) >= 1, "batch 실패 시 단건 encode 폴백"


def test_falls_back_when_batch_length_mismatch() -> None:
    """encode_batch 반환 길이가 쿼리 수와 다르면 폴백."""
    from lloydk.services.retrieval import expand_then_search

    encode_calls: list[str] = []

    def _encode(t: str):
        encode_calls.append(t)
        return [1.0, 0.0]

    def _short_batch(texts):
        return [[1.0, 0.0]]  # 1개만 반환

    store = _FakeStore()
    expand_then_search(
        store=store,
        collection="docs",
        query_text="M&A 인수합병 계약",
        encode=_encode,
        encode_batch=_short_batch,
        method="rule",
        top_k=3,
        use_reranker=False,
    )

    assert len(encode_calls) >= 1, "길이 불일치 시 단건 encode 폴백"
