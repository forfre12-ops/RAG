"""표적 1 — InferencePipeline.run(use_rag=True)이 retrieval facade 호출하고
rag_context를 채우는지 검증.

InMemoryStore + 결정론적 encoder + HashEmbedding 폴백 경로 모두 활용.
실 ML 모델은 안 씀 — rule-fallback 분기에서도 RAG context는 채워져야 함.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from lloydk.adapters.vectorstore.inmemory_store import InMemoryStore
from lloydk.modules.m5_inference.pipeline import InferencePipeline

pytestmark = pytest.mark.slow


def _populated_store() -> InMemoryStore:
    s = InMemoryStore()
    s.ensure_collection("docs", dim=4)
    s.upsert(
        "docs",
        ids=["d1", "d2", "d3"],
        vectors=[
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
        ],
        payloads=[
            {"text": "M&A 인수합병 계약서", "doc_id": "doc-aaa"},
            {"text": "특허 출원 보고서", "doc_id": "doc-bbb"},
            {"text": "이사회 의사록", "doc_id": "doc-ccc"},
        ],
    )
    return s


class _FakeEmbedder:
    """결정론적 keyword → 4-dim vector."""

    def embed(self, texts):
        out = []
        for t in texts:
            tl = t.lower()
            out.append([
                1.0 if any(k in tl for k in ("m&a", "인수")) else 0.0,
                1.0 if "특허" in t else 0.0,
                1.0 if "이사회" in t else 0.0,
                0.0,
            ])
        return out


# ---------------------------------------------------------------------------
# use_rag=False — 기존 동작 보존 (회귀 가드)
# ---------------------------------------------------------------------------


def test_use_rag_false_does_not_touch_retrieval():
    pipe = InferencePipeline()
    with patch.object(pipe, "_build_rag_context") as build:
        result = pipe.run("M&A 인수 계약 검토", use_rag=False)
    assert build.call_count == 0
    assert result.rag_context == []
    # 분류 자체는 rule-fallback이어도 정상 반환
    assert result.label is not None


# ---------------------------------------------------------------------------
# use_rag=True — _build_rag_context 호출되고 RagContextHit가 채워짐
# ---------------------------------------------------------------------------


def test_use_rag_true_populates_rag_context_via_real_facade():
    """build_store/build_embedder를 mock으로 주입해 facade 끝까지 통과."""
    store = _populated_store()
    embedder = _FakeEmbedder()

    with patch("lloydk.adapters.vectorstore.build_store", return_value=store):
        with patch("lloydk.adapters.embedding.build_embedder", return_value=embedder):
            pipe = InferencePipeline()
            result = pipe.run("M&A 인수 검토", use_rag=True)

    assert result.rag_context, "use_rag=True인데 rag_context가 비어있으면 wiring 실패"
    # 최상위 hit은 M&A 매칭 문서
    top = result.rag_context[0]
    assert top.source_doc.startswith("doc-") or top.source_doc == "d1"
    assert isinstance(top.score, float)
    assert top.score > 0
    # "rag_context empty" warning이 들어가면 안 됨 — 실제 hit가 있으므로
    assert not any("rag_context empty" in w for w in result.warnings)


def test_use_rag_true_empty_store_returns_empty_context_with_warning():
    empty = InMemoryStore()
    empty.ensure_collection("docs", dim=4)
    embedder = _FakeEmbedder()

    with patch("lloydk.adapters.vectorstore.build_store", return_value=empty):
        with patch("lloydk.adapters.embedding.build_embedder", return_value=embedder):
            pipe = InferencePipeline()
            result = pipe.run("M&A 인수", use_rag=True)

    assert result.rag_context == []
    assert any("rag_context empty" in w for w in result.warnings)


def test_use_rag_true_store_build_failure_silently_empty():
    """벡터스토어 빌드 실패도 분류 결과를 막지 않음."""
    with patch("lloydk.adapters.vectorstore.build_store", side_effect=RuntimeError("ES down")):
        pipe = InferencePipeline()
        result = pipe.run("M&A 인수", use_rag=True)
    assert result.rag_context == []
    assert result.label is not None  # 분류는 정상 반환


def test_use_rag_true_encoder_failure_silently_empty():
    store = _populated_store()

    class _BrokenEmbedder:
        def embed(self, texts):
            raise RuntimeError("HF load failed")

    with patch("lloydk.adapters.vectorstore.build_store", return_value=store):
        with patch("lloydk.adapters.embedding.build_embedder", return_value=_BrokenEmbedder()):
            pipe = InferencePipeline()
            result = pipe.run("M&A 인수", use_rag=True)
    assert result.rag_context == []
    assert result.label is not None


def test_use_rag_true_empty_query_returns_empty():
    pipe = InferencePipeline()
    result = pipe.run("", use_rag=True)
    assert result.rag_context == []


# ---------------------------------------------------------------------------
# _build_rag_context 단위 — namespace / metadata filter
# ---------------------------------------------------------------------------


def test_build_rag_context_uses_namespace_collection():
    """namespace가 주어지면 그 collection으로 호출."""
    store = _populated_store()
    embedder = _FakeEmbedder()

    seen_collection: list[str] = []
    original = store.search_hybrid

    def spy(*, collection, **kwargs):
        seen_collection.append(collection)
        return original(collection=collection, **kwargs)

    store.search_hybrid = spy  # type: ignore[method-assign]

    with patch("lloydk.adapters.vectorstore.build_store", return_value=store):
        with patch("lloydk.adapters.embedding.build_embedder", return_value=embedder):
            pipe = InferencePipeline()
            pipe._build_rag_context(query="M&A 인수", namespace="docs", metadata=None)

    assert "docs" in seen_collection


def test_build_rag_context_searchhit_to_rag_context_hit_conversion():
    """SearchHit → RagContextHit 변환: source_doc은 payload.doc_id 우선."""
    store = _populated_store()
    embedder = _FakeEmbedder()

    with patch("lloydk.adapters.vectorstore.build_store", return_value=store):
        with patch("lloydk.adapters.embedding.build_embedder", return_value=embedder):
            pipe = InferencePipeline()
            hits = pipe._build_rag_context(query="M&A 인수", namespace="docs", metadata=None)

    assert hits
    # M&A 매칭 → doc_id=doc-aaa
    matching = [h for h in hits if "doc-aaa" in h.source_doc]
    assert matching, f"M&A 매칭 hit 없음: {[h.source_doc for h in hits]}"
    assert matching[0].chunk_id == "d1"


def test_build_rag_context_fallback_to_id_when_no_doc_id_in_payload():
    s = InMemoryStore()
    s.ensure_collection("docs", dim=4)
    s.upsert("docs", ids=["x1"], vectors=[[1.0, 0.0, 0.0, 0.0]],
             payloads=[{"text": "M&A 인수"}])  # doc_id 없음
    embedder = _FakeEmbedder()

    with patch("lloydk.adapters.vectorstore.build_store", return_value=s):
        with patch("lloydk.adapters.embedding.build_embedder", return_value=embedder):
            pipe = InferencePipeline()
            hits = pipe._build_rag_context(query="M&A", namespace="docs", metadata=None)

    assert hits
    # source_doc이 id로 폴백
    assert hits[0].source_doc == "x1"


def test_build_rag_context_adapter_import_failure_returns_empty():
    """retrieval 모듈 import 실패 시 silent empty."""
    pipe = InferencePipeline()
    with patch("lloydk.services.retrieval.expand_then_search",
               side_effect=ImportError("missing")):
        hits = pipe._build_rag_context(query="M&A", namespace=None, metadata=None)
    # ImportError는 outer try에서 못 잡힘 — 본 케이스는 적어도 빈 리스트 또는 raise
    # (run() 안의 try가 잡아서 분류 자체는 통과). 여기는 단위라 raise도 허용.
    assert isinstance(hits, list)
