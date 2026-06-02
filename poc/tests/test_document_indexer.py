"""document_indexer: 업로드 문서 chunks → 검색 컬렉션 적재 + 조회 가능 검증."""
from lloydk.adapters.embedding import build_embedder
from lloydk.adapters.vectorstore.inmemory_store import InMemoryStore
from lloydk.rag.document_indexer import index_document_for_rag


def _emb():
    return build_embedder(force_hash=True)  # 결정적 hash 임베딩 (모델 다운로드 없음)


def test_indexes_chunks_and_makes_them_searchable():
    store = InMemoryStore()
    chunks = [(0, "BX-7 배터리 양산 2026년 9월 목표 단가 78달러"), (1, "양극재 니켈 92퍼센트 대외비")]
    res = index_document_for_rag(
        doc_id="doc-1", tenant_id="acme", collection="uploads-test",
        chunks=chunks, store=store, embedder=_emb(),
    )
    assert res.indexed is True
    assert res.chunk_count == 2
    assert res.vector_count == 2
    # 적재된 컬렉션에서 같은 쿼리로 조회되면 검색 대상이 된 것
    emb = _emb().embed(["BX-7 단가"])
    hits = store.search("uploads-test", emb.vectors[0], top_k=2)
    assert hits, "indexed chunk should be retrievable"
    assert any((h.payload or {}).get("doc_id") == "doc-1" for h in hits)
    assert any((h.payload or {}).get("tenant_id") == "acme" for h in hits)


def test_empty_chunks_returns_not_indexed():
    store = InMemoryStore()
    res = index_document_for_rag(
        doc_id="d", tenant_id="t", collection="c",
        chunks=[(0, "   "), (1, "")], store=store, embedder=_emb(),
    )
    assert res.indexed is False
    assert res.vector_count == 0
    assert res.warnings
