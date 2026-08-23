"""POST /synth/generate + GET /synth/queue + POST /synth/{id}/review."""

# future annotations 비활성: slowapi/pydantic forward-ref 평가에서 fail.

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from koipa.api._jwt_auth import require_auth
from koipa.api._rbac import require_role
from koipa.api.confirm import bind_authenticated_actor
from koipa.api.rate_limit import limiter
from koipa.schemas.synthesis import (
    SynthGenerateRequest,
    SynthGenerateResponse,
    SynthQueueResponse,
    SynthReviewRequest,
    SynthReviewResponse,
)
from koipa.services.synthesis_service import SynthesisService

router = APIRouter(tags=["synthesis"], dependencies=[Depends(require_auth)])


# 합성 데이터 생성은 학습셋에 편입되는 변경성 작업 → 시스템/관리 역할로 제한
# (train과 동일 정책). 검수(review)는 reviewer 권한. 조회(queue)는 broad.
@router.post(
    "/synth/generate",
    response_model=SynthGenerateResponse,
    status_code=202,
)
@limiter.limit("10/minute")
def synth_generate(
    request: Request,
    req: SynthGenerateRequest,
    auth: dict = Depends(require_role("admin", "kl_backend", "system")),
):
    # [#13] created_by 감사 신원 = 인증 principal(body 자칭 위조 차단; JWT sub 우선).
    bind_authenticated_actor(req.actor, auth)
    return SynthesisService().submit(req)


@router.get("/synth/queue", response_model=SynthQueueResponse)
def synth_queue(
    status: str = Query(default="pending", pattern=r"^(pending|approved|rejected)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return SynthesisService().queue(status=status, limit=limit, offset=offset)


@router.post(
    "/synth/{synth_id}/review",
    response_model=SynthReviewResponse,
)
def synth_review(
    synth_id: UUID,
    req: SynthReviewRequest,
    auth: dict = Depends(require_role("admin", "reviewer")),
):
    # [#13] reviewed_by 감사 신원 = 인증 principal(body 자칭 위조 차단; JWT sub 우선).
    bind_authenticated_actor(req.actor, auth)
    try:
        res = SynthesisService().review(synth_id, req)
    except ValueError as exc:
        # corrected_grade 가 현재 등급체계에 없는 코드(비활성화 등). 조용히 무시하면
        # 검수자는 고친 줄 알고 넘어가는데 학습행은 원래 등급으로 만들어진다.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if res is None:
        raise HTTPException(status_code=404, detail="synth_id not found")
    return res
