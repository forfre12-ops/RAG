"""A6 (2026-05-29): Query-expansion 적용 retrieval facade.

기존 vectorstore.search_hybrid는 단일 쿼리만 받음. 본 facade는
QueryExpansion으로 1→N 쿼리를 만든 뒤 각 쿼리로 search_hybrid를 호출,
RRF(Reciprocal Rank Fusion)로 결과를 결합한다.

표적 4 (2026-05-29 보강): RRF 결합 결과를 cross-encoder reranker로 재정렬.
1차 retrieval은 oversample(top_k × oversample_factor)로 가져온 뒤 reranker로 top_k로 좁힘.
reranker가 noop이거나 미설치면 RRF 순서 그대로 반환.

호출 패턴:
    hits = expand_then_search(
        store=vs, collection="legal", query_text="M&A 인수",
        encode=lambda t: model.encode(t),
        method="rule",  # 또는 "llm"|"hybrid"
        top_k=5,
        use_reranker=True,
    )
"""

from __future__ import annotations

import logging
from typing import Callable, Sequence

from lloydk.adapters.reranker import Reranker, get_reranker
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


def _rerank_hits(
    *,
    query_text: str,
    hits: list[SearchHit],
    reranker: Reranker,
    top_k: int,
) -> list[SearchHit]:
    """RRF 결과를 cross-encoder reranker로 재정렬.

    payload["text"]가 없는 hit은 reranker 입력에서 제외하고 원래 순서대로 뒤에 붙임.
    reranker 자체가 실패하면 RRF 순서 그대로 반환.
    """
    if not hits:
        return []

    text_hits: list[tuple[int, SearchHit]] = []
    no_text_hits: list[SearchHit] = []
    for i, h in enumerate(hits):
        text = h.payload.get("text") if h.payload else None
        if text:
            text_hits.append((i, h))
        else:
            no_text_hits.append(h)

    if not text_hits:
        return hits[:top_k]

    candidates = [h.payload["text"] for _, h in text_hits]
    try:
        ranked = reranker.rerank(query_text, candidates, top_k=top_k)
    except Exception as exc:  # noqa: BLE001
        logger.warning("reranker failed (%s) — falling back to RRF order", exc)
        return hits[:top_k]

    fused: list[SearchHit] = []
    seen_ids: set[str] = set()
    for r in ranked:
        if r.index >= len(text_hits):
            continue
        _, original_hit = text_hits[r.index]
        # reranker 점수로 덮어쓰기 — 호출자가 cross-encoder 신뢰도를 사용
        fused.append(SearchHit(id=original_hit.id, score=float(r.score), payload=dict(original_hit.payload)))
        seen_ids.add(original_hit.id)

    # text 없는 hit은 뒤에 보강 (top_k 채우기)
    for h in no_text_hits:
        if len(fused) >= top_k:
            break
        if h.id in seen_ids:
            continue
        fused.append(h)

    return fused[:top_k]


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
    use_reranker: bool = True,
    reranker: Reranker | None = None,
    oversample_factor: int = 4,
) -> list[SearchHit]:
    """query_text → 확장 N → 각 쿼리로 search_hybrid → RRF 결합 → reranker 재정렬.

    method: rule | llm | hybrid (query_expansion 모듈의 expand_* 함수와 동일)
    encode: 텍스트 → dense vector 함수 (테스트 가능성 위해 외부 주입)
    max_queries: 폭주 차단 — 확장된 쿼리 N개를 이만큼만 사용
    use_reranker: True면 settings.reranker_provider 기반으로 자동 wiring.
                  noop이면 비용 없이 RRF 순서 유지(NoopReranker의 결정론적 점수).
    reranker: 명시적으로 주입(테스트용). None이면 get_reranker()로 자동 결정.
    oversample_factor: 1차 retrieval에서 top_k × factor만큼 가져온 뒤 reranker로 좁힘.
                       reranker가 충분한 후보를 보고 재정렬 가능. 기본 4.
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

    # reranker 사용 시 1차 retrieval은 oversample
    first_k = top_k * max(1, oversample_factor) if use_reranker else top_k

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
                top_k=first_k,
                filter=filter,
            )
        except Exception as exc:  # noqa: BLE001
            # 어댑터별로 search_hybrid 미지원 시 search()로 폴백
            logger.debug("search_hybrid failed (%s) — falling back to dense search", exc)
            try:
                hits = store.search(collection=collection, query=vec, top_k=first_k, filter=filter)
            except Exception as exc2:  # noqa: BLE001
                logger.warning("dense search also failed for q=%r: %s", q, exc2)
                continue
        result_sets.append(list(hits))

    if not result_sets:
        return []
    fused = _rrf_combine(result_sets)

    if not use_reranker:
        return fused[:top_k]

    # reranker 결정 — settings 기반 자동 선택 또는 명시 주입
    rr = reranker if reranker is not None else get_reranker()
    return _rerank_hits(query_text=query_text, hits=fused, reranker=rr, top_k=top_k)
