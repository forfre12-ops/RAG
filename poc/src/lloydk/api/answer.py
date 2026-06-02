"""POST /answer — RAG 답안 합성 엔드포인트.

흐름:
  1. 요청에서 query + namespace + top_k 추출
  2. retrieval facade(expand_then_search)로 RagContextHit 수집
     - vectorstore/embedder 부재·실패는 silent → 빈 hits
  3. synthesize_answer(query, hits, grade, provider)로 LLM 답안 합성
     - LLM 부재·실패는 deterministic_fallback 응답
  4. RagAnswerResult 반환 (FastAPI가 Pydantic 직렬화)

설계 원칙:
- /classify 분리 — 본 엔드포인트는 등급 분류를 하지 않음. grade는 호출자가 별도 단계에서 제공.
- retrieval/LLM 어느 쪽이든 실패해도 200 응답 + warnings에 사유. 5xx는 진짜 예외만.
"""

import logging
import time

from fastapi import APIRouter, Depends, Request

from lloydk.api._jwt_auth import require_auth
from lloydk.api.rate_limit import limiter
from lloydk.config import settings
from lloydk.schemas.classify import RagContextHit
from lloydk.schemas.rag_answer import RagAnswerRequest, RagAnswerResult
from lloydk.services.rag_answer_service import synthesize_answer

logger = logging.getLogger(__name__)

router = APIRouter(tags=["answer"])


def _inc_rag_failure(stage: str) -> None:
    try:
        from lloydk.api.prom_metrics import RAG_CONTEXT_FAILURE_TOTAL  # noqa: PLC0415
        RAG_CONTEXT_FAILURE_TOTAL.labels(stage=stage).inc()
    except Exception:  # noqa: BLE001
        pass


def _fetch_hits(req: RagAnswerRequest) -> list[RagContextHit]:
    """retrieval facade 호출 — 모든 실패는 silent + 빈 리스트.

    분류 파이프라인(m5_inference._build_rag_context)와 동일 패턴.
    """
    collection = (req.namespace or "").strip() or getattr(
        settings, "rag_default_collection", "docs"
    )
    method = getattr(settings, "rag_query_expansion_method", "rule")

    try:
        from lloydk.adapters.embedding import build_embedder  # noqa: PLC0415
        from lloydk.adapters.vectorstore import build_store  # noqa: PLC0415
        from lloydk.services.retrieval import expand_then_search  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        logger.debug("answer: rag adapters unavailable: %s", exc)
        return []

    try:
        store = build_store()
    except Exception as exc:  # noqa: BLE001
        logger.warning("answer: vectorstore build failed: %s", exc)
        _inc_rag_failure("fetch_hits")
        return []

    try:
        embedder = build_embedder()
    except Exception as exc:  # noqa: BLE001
        logger.warning("answer: embedder build failed: %s", exc)
        _inc_rag_failure("fetch_hits")
        return []

    def _encode(t: str):
        try:
            result = embedder.embed([t])
            vectors = getattr(result, "vectors", None) or result
            return vectors[0] if vectors else []
        except Exception as exc:  # noqa: BLE001
            logger.debug("answer: encode failed: %s", exc)
            return []

    def _encode_batch(texts: list[str]):
        """§1: 확장 쿼리 N개를 1회 forward로 인코딩 — KURE p50 629ms × 4쿼리 단축."""
        if not texts:
            return []
        try:
            result = embedder.embed(texts)
            vectors = getattr(result, "vectors", None) or result
            return list(vectors)
        except Exception as exc:  # noqa: BLE001
            logger.debug("answer: encode_batch failed: %s", exc)
            return []

    # tenant 필터: 요청 tenant_id 우선, 없으면 metadata.tenant_id
    filter_ = None
    tenant = req.tenant_id
    if not tenant and isinstance(req.metadata, dict):
        tenant = req.metadata.get("tenant_id")
    if tenant:
        filter_ = {"tenant_id": tenant}

    try:
        raw_hits = expand_then_search(
            store=store,
            collection=collection,
            query_text=req.query,
            encode=_encode,
            encode_batch=_encode_batch,
            method=method,
            top_k=req.top_k,
            filter=filter_,
            use_reranker=req.use_reranker,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("answer: expand_then_search failed: %s", exc)
        _inc_rag_failure("fetch_hits")
        return []

    out: list[RagContextHit] = []
    for h in raw_hits:
        payload = h.payload or {}
        source_doc = str(payload.get("doc_id") or payload.get("source_doc") or h.id)
        out.append(RagContextHit(
            source_doc=source_doc,
            chunk_id=str(h.id),
            score=float(h.score),
            text=str(payload.get("text") or payload.get("content") or ""),
        ))
    return out


@router.post(
    "/answer",
    response_model=RagAnswerResult,
    dependencies=[Depends(require_auth)],
)
@limiter.limit("30/minute")
def answer(request: Request, req: RagAnswerRequest) -> RagAnswerResult:
    t0 = time.time()
    t_retrieve_start = time.perf_counter()
    hits = _fetch_hits(req)
    retrieve_secs = time.perf_counter() - t_retrieve_start

    t_synth_start = time.perf_counter()
    result = synthesize_answer(
        query=req.query,
        hits=hits,
        grade=req.grade,
    )
    synth_secs = time.perf_counter() - t_synth_start

    # prom_metrics 노출 — registry 없는 환경(import 실패)에서는 silent skip
    try:
        from lloydk.api import prom_metrics  # noqa: PLC0415
        prom_metrics.ANSWER_PHASE_DURATION.labels(phase="retrieve").observe(retrieve_secs)
        prom_metrics.ANSWER_PHASE_DURATION.labels(phase="synthesize").observe(synth_secs)
    except Exception:  # noqa: BLE001
        pass

    logger.info(
        "answer done: q_len=%d hits=%d fallback=%s "
        "retrieve_ms=%d synth_ms=%d elapsed_ms=%d",
        len(req.query), len(hits), result.deterministic_fallback,
        int(retrieve_secs * 1000), int(synth_secs * 1000),
        int((time.time() - t0) * 1000),
    )
    return result
