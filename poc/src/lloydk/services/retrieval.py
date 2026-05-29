"""A6 (2026-05-29): Query-expansion 적용 retrieval facade.

기존 vectorstore.search_hybrid는 단일 쿼리만 받음. 본 facade는
QueryExpansion으로 1→N 쿼리를 만든 뒤 각 쿼리로 search_hybrid를 호출,
RRF(Reciprocal Rank Fusion)로 결과를 결합한다.

호출 패턴:
    hits = expand_then_search(
        store=vs, collection="legal", query_text="M&A 인수",
        encode=lambda t: model.encode(t),
        method="rule",  # 또는 "llm"|"hybrid"
        top_k=5,
    )
"""

from __future__ import annotations

import logging
from typing import Callable, Sequence

from lloydk.adapters.vectorstore.base import SearchHit, VectorStore
from lloydk.modules.m4_training.query_expansion import (
    expand_hybrid,
    expand_llm,
    expand_rule,
)

logger = logging.getLogger(__name__)


_RRF_K = 60.0  # 표준 RRF 상수 (Cormack et al. 2009)


def _rrf_combine(result_sets: list[list[SearchHit]], *, k: float = _RRF_K) -> list[SearchHit]:
    """각 쿼리의 결과를 RRF로 결합. 같은 id가 여러 쿼리에 등장하면 점수 누적."""
    fused: dict[str, SearchHit] = {}
    for hits in result_sets:
        for rank, hit in enumerate(hits, start=1):
            inc = 1.0 / (k + rank)
            if hit.id in fused:
                prev = fused[hit.id]
                fused[hit.id] = SearchHit(id=prev.id, score=prev.score + inc, payload=prev.payload)
            else:
                fused[hit.id] = SearchHit(id=hit.id, score=inc, payload=dict(hit.payload))
    return sorted(fused.values(), key=lambda h: h.score, reverse=True)


def expand_then_search(
    *,
    store: VectorStore,
    collection: str,
    query_text: str,
    encode: Callable[[str], Sequence[float]],
    method: str = "rule",
    top_k: int = 5,
    filter: dict | None = None,
    max_queries: int = 5,
) -> list[SearchHit]:
    """query_text → 확장 N → 각 쿼리로 search_hybrid → RRF 결합.

    method: rule | llm | hybrid (query_expansion 모듈의 expand_* 함수와 동일)
    encode: 텍스트 → dense vector 함수 (테스트 가능성 위해 외부 주입)
    max_queries: 폭주 차단 — 확장된 쿼리 N개를 이만큼만 사용
    """
    if not query_text or not query_text.strip():
        return []

    if method == "llm":
        expansion = expand_llm(query_text)
    elif method == "hybrid":
        expansion = expand_hybrid(query_text)
    else:
        expansion = expand_rule(query_text)

    queries = expansion.expanded[:max_queries] if expansion.expanded else [query_text]
    logger.debug("query expansion: original=%r method=%s n=%d",
                 query_text, expansion.method, len(queries))

    result_sets: list[list[SearchHit]] = []
    for q in queries:
        try:
            vec = list(encode(q))
        except Exception as exc:  # noqa: BLE001
            logger.warning("encode failed for q=%r: %s", q, exc)
            continue
        try:
            hits = store.search_hybrid(
                collection=collection,
                query_text=q,
                query_vec=vec,
                top_k=top_k,
                filter=filter,
            )
        except Exception as exc:  # noqa: BLE001
            # 어댑터별로 search_hybrid 미지원 시 search()로 폴백
            logger.debug("search_hybrid failed (%s) — falling back to dense search", exc)
            try:
                hits = store.search(collection=collection, query=vec, top_k=top_k, filter=filter)
            except Exception as exc2:  # noqa: BLE001
                logger.warning("dense search also failed for q=%r: %s", q, exc2)
                continue
        result_sets.append(list(hits))

    if not result_sets:
        return []
    fused = _rrf_combine(result_sets)
    return fused[:top_k]
