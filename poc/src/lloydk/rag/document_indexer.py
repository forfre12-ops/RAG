"""업로드 문서 → RAG 검색 인덱스 브리지.

업로드 인제스트(DocumentIngestionService)는 분류용으로 DB chunks까지만 적재한다.
RAG 검색(/answer)은 별도 벡터 컬렉션을 조회하므로, 업로드 문서는 그대로는
검색되지 않는다. 이 모듈이 그 chunks를 임베딩해 검색 컬렉션에 upsert한다.

설계 원칙:
- 평가 코퍼스("docs")를 오염시키지 않도록 **별도 컬렉션**(호출자가 지정)에 적재한다.
- tenant 제거: 격리는 KL 포털 전담 — payload에 tenant 스코프 없음(무스코핑 전역 검색).
- chunks는 (chunk_index, text) 시퀀스로 주입 → DB 비의존, 단위 테스트 격리 가능.
- 임베딩/스토어 실패는 indexed=False + warnings로 조용히 반환(업로드 자체는 성공 유지).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

# #15: v1 split(순수 문자 단위)은 한국어 조항/문장 중간을 절단하고 heading_path
#      메타를 유실한다. 운영 권장 split_v2(헤딩·문장 경계 보존)로 전환한다.
from lloydk.config import settings
from lloydk.modules.m2_preprocess import split_v2


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
    collection: str,
    chunks: Sequence[tuple[int, str]],
    store=None,
    embedder=None,
) -> DocIndexResult:
    """문서 1건의 chunks를 검색 컬렉션에 적재.

    Args:
        doc_id: 문서 식별자(검색 결과 doc_id/citation에 사용).
        collection: 적재 대상 검색 컬렉션(평가용 "docs"와 분리할 것).
        chunks: (chunk_index, text) 시퀀스.
        store/embedder: 미주입 시 build_store()/build_embedder() 기본 결정.
    """
    # #15: 입력 (chunk_index, text)를 split_v2로 재분할 — 헤딩/문장 경계를 보존하고
    #      각 (서브)청크의 heading_path 메타를 회수한다. 입력 chunk_index는 출처
    #      순서를 유지하기 위해 src_idx로 보존하고, split 결과는 0..N으로 재번호한다.
    #      청크 크기 정책은 기존 설정값(rag_index_chunk_size/overlap)을 그대로 사용.
    norm: list[tuple[int, str, list[str]]] = []  # (chunk_idx, text, heading_path)
    for _src_idx, raw in chunks:
        body = (raw or "").strip()
        if not body:
            continue
        sub_chunks = split_v2(
            body,
            size=settings.rag_index_chunk_size,
            overlap=settings.rag_index_chunk_overlap,
            respect_heading=True,
            respect_sentence=True,
        )
        # split_v2가 (이론상) 빈 결과를 주면 원문 그대로 1청크로 폴백.
        if not sub_chunks:
            norm.append((len(norm), body, []))
            continue
        for c in sub_chunks:
            norm.append((len(norm), c.text, list(c.heading_path)))
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

    texts = [t for _, t, _ in norm]
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

    ids = [f"{doc_id}:c{idx}" for idx, _, _ in norm]
    payloads = [
        # #15: split_v2가 부여한 섹션 경로(heading_path)를 payload에 적재 —
        #      검색·표시·reranking에 활용 가능. 필드명은 indexer.py 컨벤션과 일치.
        {
            "doc_id": str(doc_id),
            "chunk_idx": idx,
            "text": txt,
            "heading_path": heading_path,
        }
        for idx, txt, heading_path in norm
    ]
    try:
        n = store.upsert(collection, ids, emb.vectors, payloads)
    except Exception as exc:  # noqa: BLE001
        return DocIndexResult(False, collection, len(norm), 0, [f"upsert failed: {exc}"])

    return DocIndexResult(True, collection, len(norm), int(n), warns)
