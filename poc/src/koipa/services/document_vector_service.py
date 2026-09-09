"""문서 대표 벡터 색인 — 청크 임베딩의 평균을 한 벌 만들어 저장한다.

■ 왜 서비스로 떼어 두는가

  celery 태스크 안에 로직을 두면 시험이 워커를 띄워야 한다. 여기는 순수하게 DB 와
  임베더만 상대하므로 시험에서 그대로 부를 수 있고, 태스크는 이 함수를 부르기만 한다.

■ 색인 실패는 등급 판정을 막지 않는다

  유사문서 조회는 **참고 기능**이다. 임베딩이 실패하거나 pgvector 가 없어도 업로드·분류·
  검수는 그대로 돌아야 한다. 그래서 호출부(태스크·인제스트)는 예외를 흡수하고, 이 함수는
  "무엇을 했는지"를 dict 로 돌려준다 — 조용한 실패가 아니라 세어지는 실패여야 한다.

■ 비용 (실측 2026-09-09, KURE-v1 · CPU 6스레드)

    청크당 0.51초 · 100쪽 문서(238청크) 약 2분

  그래서 업로드 동기 경로에 얹지 않는다(워커 큐로 보낸다). 같은 본문을 다시 올리면
  content_sha256 이 같아 통째로 건너뛴다 — 재업로드가 2분을 다시 쓰지 않게.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _mean_unit_vector(vectors: list[list[float]]) -> list[float]:
    """청크 벡터들의 평균을 단위벡터로. 문서 하나를 대표하는 방향만 남긴다.

    길이가 서로 다른 문서를 같은 자로 재려면 크기를 없애야 한다. 코사인 거리 자체가
    크기에 둔감하지만, 저장 시점에 정규화해 두면 나중에 내적으로 바꿔도 같은 값이 된다.
    """
    if not vectors:
        return []
    dim = len(vectors[0])
    acc = [0.0] * dim
    for v in vectors:
        if len(v) != dim:          # 임베더가 길이를 흔들면 평균이 조용히 망가진다
            raise ValueError(f"청크 임베딩 차원이 섞였다: {len(v)} != {dim}")
        for i, x in enumerate(v):
            acc[i] += x
    n = float(len(vectors))
    mean = [x / n for x in acc]
    norm = sum(x * x for x in mean) ** 0.5
    if norm == 0.0:
        return mean
    return [x / norm for x in mean]


def index_document(
    doc_id: str,
    *,
    db_factory: Any | None = None,
    store: Any | None = None,
    embedder: Any | None = None,
    force: bool = False,
) -> dict:
    """문서 하나를 색인한다. 무엇을 했는지 dict 로 돌려준다(예외를 삼키지 않는다).

    반환 status:
        indexed     새로 넣었거나 갱신했다
        skipped     본문이 그대로다(content_sha256 일치) — force=True 로 무시할 수 있다
        no_chunks   청크가 없다. 추출이 실패한 문서라 색인할 본문이 없다
    """
    from koipa.adapters.embedding import build_embedder  # noqa: PLC0415
    from koipa.adapters.vectorstore import DocumentVectorStore  # noqa: PLC0415
    from koipa.db.session import SessionLocal  # noqa: PLC0415
    from koipa.repositories.chunk_repo import ChunkRepo  # noqa: PLC0415

    db_factory = db_factory or SessionLocal
    store = store or DocumentVectorStore()

    with db_factory() as db:
        chunks = ChunkRepo(db).get_by_doc_id(doc_id)
    texts = [c.content for c in chunks if getattr(c, "content", None)]
    if not texts:
        logger.info("문서 벡터 색인 건너뜀 — 청크 없음: doc_id=%s", doc_id)
        return {"doc_id": str(doc_id), "status": "no_chunks", "chunk_count": 0}

    sha = hashlib.sha256("\n".join(texts).encode("utf-8")).hexdigest()
    if not force and not store.needs_index(doc_id, sha):
        return {"doc_id": str(doc_id), "status": "skipped", "chunk_count": len(texts)}

    embedder = embedder or build_embedder()
    result = embedder.embed(texts)
    vectors = [list(v) for v in result.vectors]
    if not vectors:
        return {"doc_id": str(doc_id), "status": "no_chunks", "chunk_count": 0}

    store.upsert(
        doc_id=doc_id,
        embedding=_mean_unit_vector(vectors),
        model=getattr(result, "model", "") or getattr(embedder, "name", "unknown"),
        chunk_count=len(vectors),
        content_sha256=sha,
    )
    logger.info("문서 벡터 색인 완료: doc_id=%s 청크=%d", doc_id, len(vectors))
    return {"doc_id": str(doc_id), "status": "indexed", "chunk_count": len(vectors)}
