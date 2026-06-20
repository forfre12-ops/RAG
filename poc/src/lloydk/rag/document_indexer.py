"""업로드 문서 → RAG 검색 인덱스 브리지.

업로드 인제스트(DocumentIngestionService)는 분류용으로 DB chunks까지만 적재한다.
RAG 검색(/answer)은 별도 벡터 컬렉션을 조회하므로, 업로드 문서는 그대로는
검색되지 않는다. 이 모듈이 그 chunks를 임베딩해 검색 컬렉션에 upsert한다.

설계 원칙:
- 평가 코퍼스("docs")를 오염시키지 않도록 **별도 컬렉션**(호출자가 지정)에 적재한다.
- payload에 tenant_id를 넣어 /answer의 tenant 필터(filter={"tenant_id": ...})와 정합.
- chunks는 (chunk_index, text) 시퀀스로 주입 → DB 비의존, 단위 테스트 격리 가능.
- 임베딩/스토어 실패는 indexed=False + warnings로 조용히 반환(업로드 자체는 성공 유지).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence


@dataclass
class DocIndexResult:
    indexed: bool
    collection: str
    chunk_count: int
    vector_count: int
    warnings: list[str] = field(default_factory=list)


def index_document_for_rag(
    *,
    doc_id: str,
    tenant_id: str,
    collection: str,
    chunks: Sequence[tuple[int, str]],
    store=None,
    embedder=None,
) -> DocIndexResult:
    """문서 1건의 chunks를 검색 컬렉션에 적재.

    Args:
        doc_id: 문서 식별자(검색 결과 doc_id/citation에 사용).
        tenant_id: payload에 기록 → tenant 필터 정합.
        collection: 적재 대상 검색 컬렉션(평가용 "docs"와 분리할 것).
        chunks: (chunk_index, text) 시퀀스.
        store/embedder: 미주입 시 build_store()/build_embedder() 기본 결정.
    """
    norm = [(int(idx), txt) for idx, txt in chunks if (txt or "").strip()]
    if not norm:
        return DocIndexResult(False, collection, 0, 0, ["no non-empty chunks"])

    warns: list[str] = []
    try:
        if store is None:
            from lloydk.adapters.vectorstore import build_store  # noqa: PLC0415
            store = build_store()
        if embedder is None:
            from lloydk.adapters.embedding import build_embedder  # noqa: PLC0415
            embedder = build_embedder()
    except Exception as exc:  # noqa: BLE001
        return DocIndexResult(False, collection, len(norm), 0, [f"adapter build failed: {exc}"])

    texts = [t for _, t in norm]
    try:
        emb = embedder.embed(texts)
    except Exception as exc:  # noqa: BLE001
        return DocIndexResult(False, collection, len(norm), 0, [f"embedding failed: {exc}"])

    try:
        store.ensure_collection(collection, emb.dim)
    except Exception as exc:  # noqa: BLE001
        return DocIndexResult(False, collection, len(norm), 0, [f"ensure_collection failed: {exc}"])

    # #17: 재인덱싱 고아 청크 제거 — 같은 doc_id를 더 적은 청크로 재업로드하면
    # 이전 {doc_id}:c{N} 청크가 upsert만으로는 stale로 남는다. upsert 전에 해당
    # doc_id의 기존 청크를 선삭제해 멱등 재인덱싱을 보장한다.
    # delete 미구현 스토어(구버전)는 best-effort로 건너뛰고 warning만 남긴다.
    delete = getattr(store, "delete", None)
    if callable(delete):
        try:
            delete(collection, filter={"doc_id": str(doc_id)})
        except Exception as exc:  # noqa: BLE001
            warns.append(f"stale chunk pre-delete failed: {exc}")
    else:
        warns.append("store has no delete(); stale chunks may remain on re-index")

    ids = [f"{doc_id}:c{idx}" for idx, _ in norm]
    payloads = [
        {"doc_id": str(doc_id), "chunk_idx": idx, "tenant_id": tenant_id, "text": txt}
        for idx, txt in norm
    ]
    try:
        n = store.upsert(collection, ids, emb.vectors, payloads)
    except Exception as exc:  # noqa: BLE001
        return DocIndexResult(False, collection, len(norm), 0, [f"upsert failed: {exc}"])

    return DocIndexResult(True, collection, len(norm), int(n), warns)
