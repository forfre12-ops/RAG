"""POST /golden/build + GET /golden/jobs/{id} — 통합 골든셋 빌더 (G3b).

빌드(생성·변경) = admin/kl_backend/system. 상태 조회 = reviewer 포함 broad.
정본(classification_gold.jsonl)은 직접 변경하지 않고 run-스코프 후보 파일에 쓴다.
human_review 승격은 별개 경로(import_review_corrections, 지재원 관리자).
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from lloydk.api._jwt_auth import require_auth
from lloydk.api._rbac import require_role
from lloydk.schemas.golden import (
    GoldenBuildRequest,
    GoldenBuildResponse,
    GoldenBuildStatus,
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
