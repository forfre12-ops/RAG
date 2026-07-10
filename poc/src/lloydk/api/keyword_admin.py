"""태깅 키워드 관리 API — /admin/keywords CRUD (FUN-023).

목록(GET)은 admin·reviewer, 변경(POST/PATCH)은 admin 전용. 삭제는 하드 삭제 대신
PATCH is_active=false(소프트 삭제). 변경 후 서빙 룰엔진이 핫리로드되어 재기동 없이 반영된다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from lloydk.api._rbac import require_role
from lloydk.schemas.keyword_admin import (
    KeywordCreateRequest,
    KeywordListResponse,
    KeywordMutationResponse,
    KeywordUpdateRequest,
)
from lloydk.services.keyword_admin_service import KeywordAdminError, KeywordAdminService

router = APIRouter(prefix="/admin", tags=["admin"])

_ADMIN_ONLY = [Depends(require_role("admin"))]
_ADMIN_OR_REVIEWER = [Depends(require_role("admin", "reviewer"))]


@router.get(
    "/keywords",
    response_model=KeywordListResponse,
    dependencies=_ADMIN_OR_REVIEWER,
    summary="태깅 키워드 목록 조회 (등급·요소·활성 필터)",
)
def list_keywords(
    grade: str | None = Query(default=None, description="등급 코드 필터(TS·S1·S2·S3)"),
    factor: str | None = Query(default=None, description="평가요소 코드 필터"),
    active_only: bool = Query(default=True, description="활성 키워드만(False면 비활성 포함)"),
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> KeywordListResponse:
    return KeywordAdminService().list(
        grade=grade, factor=factor, active_only=active_only, limit=limit, offset=offset
    )


@router.post(
    "/keywords",
    response_model=KeywordMutationResponse,
    dependencies=_ADMIN_ONLY,
    status_code=201,
    summary="태깅 키워드 추가 (룰엔진 핫리로드)",
)
def create_keyword(req: KeywordCreateRequest) -> KeywordMutationResponse:
    try:
        return KeywordAdminService().create(req)
    except KeywordAdminError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


@router.patch(
    "/keywords/{keyword_id}",
    response_model=KeywordMutationResponse,
    dependencies=_ADMIN_ONLY,
    summary="태깅 키워드 수정·비활성 (is_active=false=소프트 삭제, 룰엔진 핫리로드)",
)
def update_keyword(keyword_id: int, req: KeywordUpdateRequest) -> KeywordMutationResponse:
    try:
        return KeywordAdminService().update(keyword_id, req)
    except KeywordAdminError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
