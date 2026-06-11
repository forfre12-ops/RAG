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

from lloydk.api._jwt_auth import require_auth, resolve_effective_tenant
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


def _fetch_hits(req: RagAnswerRequest, request: Request) -> list[RagContextHit]:
    """retrieval facade 호출 — 모든 실패는 silent + 빈 리스트.

    분류 파이프라인(m5_inference._build_rag_context)와 동일 패턴.

    보안(H7/H8): body의 tenant_id를 그대로 신뢰하지 않고
    ``resolve_effective_tenant``로 인증 컨텍스트에 결속한 *유효 tenant*로만
    벡터 검색을 스코프한다(객체 수준 권한 = 교차테넌트 누출 차단). 또한
    검색 결과를 payload.tenant_id == eff_tenant 로 사후 필터해, 벡터스토어
    filter 미적용/부분적용(어댑터별 한계)이어도 fail-CLOSED를 보장한다.
    eff_tenant가 None(단일 공유키 레거시·단일테넌트)인 경우 사후 필터는
    payload에 tenant_id가 없는(=단일테넌트) hit만 통과시켜, 멀티테넌트 문서가
    무필터로 새어나가지 않게 한다.
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

    # 보안: body tenant_id(또는 metadata.tenant_id)를 인증 컨텍스트에 결속.
    # 불일치 시 resolve_effective_tenant가 403. 이후 검색은 eff_tenant로만 스코프.
    claimed_tenant = req.tenant_id
    if not claimed_tenant and isinstance(req.metadata, dict):
        claimed_tenant = req.metadata.get("tenant_id")
    eff_tenant = resolve_effective_tenant(request, claimed_tenant)

    filter_ = {"tenant_id": eff_tenant} if eff_tenant else None

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
    dropped = 0
    for h in raw_hits:
        payload = h.payload or {}
        # 방어적 사후 필터(fail-CLOSED): 벡터스토어 filter가 미적용/부분적용이어도
        # eff_tenant와 일치하지 않는 hit는 버린다. eff_tenant=None이면 payload에
        # tenant_id가 있는(멀티테넌트) hit를 모두 버려 무필터 누출을 차단.
        hit_tenant = payload.get("tenant_id")
        if hit_tenant != eff_tenant:
            dropped += 1
            continue
        source_doc = str(payload.get("doc_id") or payload.get("source_doc") or h.id)
        out.append(RagContextHit(
            source_doc=source_doc,
            chunk_id=str(h.id),
            score=float(h.score),
            text=str(payload.get("text") or payload.get("content") or ""),
        ))
    if dropped:
        logger.warning(
            "answer: dropped %d cross-tenant hit(s) (eff_tenant=%r) — fail-CLOSED",
            dropped, eff_tenant,
        )
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
    hits = _fetch_hits(req, request)
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
