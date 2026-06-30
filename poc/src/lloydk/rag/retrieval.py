"""RAG retrieval facade — query expansion + hybrid search + RRF + reranker.

도메인 독립 코어. 어댑터(vectorstore/embedder/reranker)만 있으면 어느 프로젝트에서나 사용 가능.

사용 예:
    from lloydk.rag.retrieval import expand_then_search

    hits = expand_then_search(
        store=vs, collection="legal", query_text="M&A 인수",
        encode=lambda t: model.encode(t),
        method="rule",
        top_k=5,
    )
"""

from __future__ import annotations

import logging
from typing import Callable, Sequence

from lloydk.adapters.reranker import Reranker, get_reranker
from lloydk.adapters.vectorstore.base import SearchHit, VectorStore
from lloydk.rag.query_expansion import expand_hybrid, expand_llm, expand_rule

logger = logging.getLogger(__name__)


def _record_hybrid_degrade() -> None:
    """hybrid→dense 폴백을 prom counter로 가시화.

    retrieval은 도메인 독립 코어이므로 lloydk.api에 강결합하지 않는다. 메트릭은
    best-effort로만 증가시키고(미가용·import 실패 무시), 가시성의 1차 보장은 호출부
    WARNING 로그가 담당한다.
    """
    try:
        from lloydk.api.prom_metrics import RAG_CONTEXT_FAILURE_TOTAL  # noqa: PLC0415

        RAG_CONTEXT_FAILURE_TOTAL.labels(stage="hybrid_degrade").inc()
    except Exception:  # noqa: BLE001
        pass


def _record_fallback(stage: str) -> None:
    """검색 품질 저하 폴백(reranker→RRF, encode_batch→single, encode→skip)을 가시화.

    무음으로 떨어지면 검색 품질이 조용히 나빠진다 — 호출부 WARNING 로그 + 이 best-effort
    counter(stage 라벨)로 운영 대시보드에 노출. retrieval은 도메인 독립 코어라 lazy import.
    """
    try:
        from lloydk.api.prom_metrics import RAG_CONTEXT_FAILURE_TOTAL  # noqa: PLC0415

        RAG_CONTEXT_FAILURE_TOTAL.labels(stage=stage).inc()
    except Exception:  # noqa: BLE001
        pass


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
    # 지연 import — 도메인 독립 코어(rag/)가 obs에 top-level로 결합되지 않게. span은 no-op 안전.
    from lloydk.obs.otel import span  # noqa: PLC0415
    try:
        with span(
            "ml.reranker.rank",
            n_candidates=len(candidates),
            top_k=top_k,
            reranker_name=getattr(reranker, "name", "unknown"),
        ):
            ranked = reranker.rerank(query_text, candidates, top_k=top_k)
    except Exception as exc:  # noqa: BLE001
        logger.warning("reranker failed (%s) — falling back to RRF order", exc)
        _record_fallback("reranker_fallback")
        return hits[:top_k]

    fused: list[SearchHit] = []
    seen_ids: set[str] = set()
    for r in ranked:
        if r.index >= len(text_hits):
            continue
        _, original_hit = text_hits[r.index]
        fused.append(SearchHit(id=original_hit.id, score=float(r.score), payload=dict(original_hit.payload)))
        seen_ids.add(original_hit.id)

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
    encode_batch: Callable[[list[str]], list[Sequence[float]]] | None = None,
    method: str = "rule",
    top_k: int = 5,
    filter: dict | None = None,
    max_queries: int = 5,
    use_reranker: bool = True,
    reranker: Reranker | None = None,
    oversample_factor: int = 4,
    synonyms: dict[str, list[str]] | None = None,
) -> list[SearchHit]:
    """query_text → 확장 N → 각 쿼리로 search_hybrid → RRF 결합 → reranker 재정렬.

    Args:
        synonyms: rule expansion에 사용할 도메인 동의어 사전.
                  None이면 query_expansion 모듈의 기본 사전 사용.
                  다른 프로젝트에서 도메인 사전을 주입해 재사용 가능.
        method: rule | llm | hybrid
        encode: 텍스트 → dense vector 함수 (테스트 가능성 위해 외부 주입). 단건 폴백.
        encode_batch: 선택. 확장된 쿼리 N개를 1회 forward로 인코딩.
        max_queries: 폭주 차단 — 확장된 쿼리 N개를 이만큼만 사용
        use_reranker: True면 settings.reranker_provider 기반으로 자동 wiring.
        reranker: 명시적으로 주입(테스트용). None이면 get_reranker()로 자동 결정.
        oversample_factor: 1차 retrieval에서 top_k × factor만큼 가져온 뒤 reranker로 좁힘.
    """
    if not query_text or not query_text.strip():
        return []

    if method == "llm":
        expansion = expand_llm(query_text)
    elif method == "hybrid":
        expansion = expand_hybrid(query_text, synonyms=synonyms)
    else:
        expansion = expand_rule(query_text, synonyms=synonyms)

    queries = expansion.expanded[:max_queries] if expansion.expanded else [query_text]
    logger.debug("query expansion: original=%r method=%s n=%d",
                 query_text, expansion.method, len(queries))

    first_k = top_k * max(1, oversample_factor) if use_reranker else top_k

    vecs: list[Sequence[float] | None] = [None] * len(queries)
    # 지연 import — 도메인 독립 코어 디커플 유지. span은 OTel 미활성 시 no-op.
    from lloydk.obs.otel import span  # noqa: PLC0415
    if encode_batch is not None:
        try:
            with span("ml.embedding.encode_batch", n_queries=len(queries), method=expansion.method):
                batch_result = encode_batch(queries)
            if len(batch_result) == len(queries):
                vecs = [list(v) for v in batch_result]
            else:
                logger.warning(
                    "encode_batch length mismatch (got %d, expected %d) — single fallback",
                    len(batch_result), len(queries),
                )
                _record_fallback("encode_batch_fallback")
        except Exception as exc:  # noqa: BLE001
            logger.warning("encode_batch failed (%s) — single fallback", exc)
            _record_fallback("encode_batch_fallback")

    result_sets: list[list[SearchHit]] = []
    for i, q in enumerate(queries):
        vec = vecs[i]
        if vec is None:
            try:
                with span("ml.embedding.encode", method=expansion.method):
                    vec = list(encode(q))
            except Exception as exc:  # noqa: BLE001
                logger.warning("encode failed for q=%r: %s", q, exc)
                _record_fallback("encode_fallback")
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
            # hybrid→dense 저하는 BM25 절반이 조용히 사라지는 것이므로 관측 가능해야 한다.
            # WARNING 로그 + prom counter(stage='hybrid_degrade')로 가시화.
            logger.warning(
                "search_hybrid failed (%s) — falling back to dense search (BM25 path lost)", exc
            )
            _record_hybrid_degrade()
            try:
                hits = store.search(collection=collection, query=vec, top_k=first_k, filter=filter)
            except Exception as exc2:  # noqa: BLE001
                logger.warning("dense search also failed for q=%r: %s", q, exc2)
                continue
        result_sets.append(list(hits))

    if not result_sets:
        return []
    # #14(이중 RRF 방지): store.search_hybrid가 이미 BM25+dense를 RRF 융합한 점수를
    # 반환한다. 단일 result_set(확장쿼리 1개)일 때 outer RRF를 또 적용하면 store의
    # 원점수가 무의미한 1/(60+rank)로 평탄화되므로, 그대로 원점수·순서를 보존한다.
    # 멀티쿼리(확장 2개+)일 때만 outer RRF로 rank 기반 멀티쿼리 융합을 수행한다.
    if len(result_sets) == 1:
        fused = result_sets[0]
    else:
        fused = _rrf_combine(result_sets)

    if not use_reranker:
        return fused[:top_k]

    rr = reranker if reranker is not None else get_reranker()
    return _rerank_hits(query_text=query_text, hits=fused, reranker=rr, top_k=top_k)
