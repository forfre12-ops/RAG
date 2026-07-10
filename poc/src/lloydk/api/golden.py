"""POST /golden/build + GET /golden/jobs/{id} — 통합 골든셋 빌더 (G3b).

빌드(생성·변경) = admin/kl_backend/system. 상태 조회 = reviewer 포함 broad.
정본(classification_gold.jsonl)은 직접 변경하지 않고 run-스코프 후보 파일에 쓴다.
human_review 승격은 별개 경로(import_review_corrections, 지재원 관리자).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from lloydk.api._jwt_auth import require_auth
from lloydk.api._rbac import require_role
from lloydk.api.confirm import resolve_actor_user_id
from lloydk.golden_tiers import is_human_reviewer
from lloydk.schemas.golden import (
    GoldenBuildRequest,
    GoldenBuildResponse,
    GoldenBuildStatus,
    GoldenSignoffRequest,
    GoldenSignoffResponse,
)
from lloydk.services.golden_build_service import GoldenBuildService

router = APIRouter(tags=["golden"], dependencies=[Depends(require_auth)])


@router.post(
    "/golden/build",
    response_model=GoldenBuildResponse,
    dependencies=[Depends(require_role("admin", "kl_backend", "system"))],
)
def golden_build(request: Request, req: GoldenBuildRequest) -> GoldenBuildResponse:
    return GoldenBuildService().submit(req)


@router.get(
    "/golden/jobs/{job_id}",
    response_model=GoldenBuildStatus,
    dependencies=[Depends(require_role("admin", "kl_backend", "reviewer", "system"))],
)
def golden_job_status(job_id: UUID) -> GoldenBuildStatus:
    st = GoldenBuildService().get_status(job_id)
    if st is None:
        raise HTTPException(status_code=404, detail="golden build job not found")
    return st


@router.get(
    "/golden/jobs/{job_id}/review.html",
    response_class=HTMLResponse,
    dependencies=[Depends(require_role("admin", "kl_backend", "reviewer", "system"))],
)
def golden_job_review_html(job_id: UUID) -> HTMLResponse:
    """빌드 잡의 후보를 지재원 관리자 검수용 인터랙티브 HTML로 반환."""
    html = GoldenBuildService().render_review(job_id)
    if html is None:
        raise HTTPException(status_code=404, detail="golden build review not found")
    return HTMLResponse(content=html)


@router.get(
    "/golden/jobs/{job_id}/signoff.html",
    response_class=HTMLResponse,
    dependencies=[Depends(require_role("admin", "kl_backend", "reviewer"))],
)
def golden_job_signoff_html(job_id: UUID) -> HTMLResponse:
    """빌드 잡의 gold 후보를 화면 서명용 인터랙티브 HTML로 반환(골든셋 검수).

    review.html(보기 전용)과 달리 승인/등급변경/거부 폼 + 제출 버튼을 붙여, 제출 시
    POST /golden/jobs/{id}/signoff 로 서명을 보낸다. 서명 캡처 UI 갭 해소.
    """
    html = GoldenBuildService().render_signoff(job_id)
    if html is None:
        raise HTTPException(status_code=404, detail="golden build signoff view not found")
    return HTMLResponse(content=html)


@router.post(
    "/golden/jobs/{job_id}/signoff",
    response_model=GoldenSignoffResponse,
)
def golden_job_signoff(
    job_id: UUID,
    req: GoldenSignoffRequest,
    auth: dict = Depends(require_role("admin", "reviewer", "kl_backend")),
) -> GoldenSignoffResponse:
    """검수 결정(승인/등급변경/거부)을 골든 후보에 적용해 locked_gold_eval 로 승격(골든셋 검수 서명).

    [신원 무결성] reviewer_id 는 인증 신원(jwt sub 우선)으로 확정한다 — 클라 자칭 위조 차단
    (/confirm 과 동일한 resolve_actor_user_id). 머신/플레이스홀더 reviewer 는 is_human_reviewer
    로 사전 거부(promote_to_locked 도 재차 거부하나 명시적 403 로 UX 개선). 정본 미변경, 라이브
    readiness 반영은 publish=True 일 때만(기본 미리보기).
    """
    reviewer_id, overridden = resolve_actor_user_id(req.actor.user_id, auth)
    if not is_human_reviewer(reviewer_id):
        raise HTTPException(
            status_code=403,
            detail=f"reviewer_id는 실계정이어야 합니다(머신/플레이스홀더 거부): {reviewer_id!r}",
        )
    result = GoldenBuildService().apply_signoff(
        job_id,
        req.decisions,
        reviewer_id=reviewer_id,
        publish=req.publish,
        dual_for_upper=req.dual_for_upper,
    )
    if result is None:
        raise HTTPException(
            status_code=404, detail="golden build job not found (or no gold candidates)"
        )
    return GoldenSignoffResponse(
        locked=result["locked"],
        rejected=result["rejected"],
        locked_by_grade=result["locked_by_grade"],
        rejected_reasons=result["rejected_reasons"],
        readiness=result["readiness"],
        published=result["published"],
        reviewer_id=reviewer_id,
        overridden=overridden,
    )
