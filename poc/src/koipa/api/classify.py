import time
from fastapi import APIRouter, Depends, Request

from koipa.api._jwt_auth import require_auth
from koipa.api.rate_limit import limiter
from koipa.schemas.classify import ClassifyRequest, ClassifyResponse, kl_wire_projection
from koipa.services.classify_service import ClassifyService
from koipa.services.regulation_evidence_service import (
    regulation_reference_for_kl_wire,
    regulation_summary_for_kl_wire,
)

router = APIRouter(tags=["classify"])


def get_service() -> ClassifyService:
    return ClassifyService.get_instance()


@router.post("/classify", response_model=ClassifyResponse, dependencies=[Depends(require_auth)])
@limiter.limit("60/minute")
def classify(request: Request, req: ClassifyRequest, svc: ClassifyService = Depends(get_service)):
    t0 = time.time()
    # tenant 제거: 격리는 KL 포털 전담 → 무스코프 분류.
    result = svc.classify(req)
    # ``kl_wire_projection`` deliberately returns the public ClassifyOutcome
    # shape, which has no internal-only elapsed_ms field.  Measure on the
    # internal response before projecting so KL calls cannot try to mutate the
    # public model after that boundary.
    result.elapsed_ms = int((time.time() - t0) * 1000)
    # KL은 동기·비동기·콜백 중 어느 경로를 택해도 같은 Agent 계약을 받아야 한다.
    # 분류 결과는 RAG/LLM 보조 기능보다 먼저 완성되어 있으므로, 규정 검색이나 요약이
    # 비어도 label·룰 불일치·근거를 포함한 Agent 답 자체를 막지 않는다.
    if getattr(request.state, "auth_role", None) == "kl_backend":
        reference = regulation_reference_for_kl_wire(result.doc_id)
        wire = kl_wire_projection(
            result,
            regulation_reference=reference,
            regulation_summary=regulation_summary_for_kl_wire(result.doc_id, reference),
        )
        # /classify 는 ClassifyResponse 계약(요청 처리 시간 포함)을 유지한다. 투영은
        # KL 에 보이면 안 되는 정책/그림자 진단값을 걷는 단계일 뿐이며, elapsed_ms 는
        # 의사결정 자료가 아닌 이 요청의 처리 시간이다.
        result = result.model_copy(update={
            "rule_grade": wire.rule_grade,
            "model_grade": wire.model_grade,
            "grade_candidates": wire.grade_candidates,
            "grade_candidates_reason": wire.grade_candidates_reason,
            "regulation_reference": wire.regulation_reference,
            "regulation_summary": wire.regulation_summary,
            "explanation_status": wire.explanation_status,
            "fallback_reason": wire.fallback_reason,
            "automation_assessment": None,
            "factor_shadow": None,
            "policy_shadow": None,
        })
    return result
