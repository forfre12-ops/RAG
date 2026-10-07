"""GET/PUT /schema/grades — 등급체계 조회·변경 + 재학습 필요성 응답."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from koipa.api._jwt_auth import require_auth
from koipa.schemas.schema_admin import (
    GradesGetResponse,
    GradesPutRequest,
    GradesPutResponse,
)
from koipa.services.schema_admin_service import GradeSchemaBlocked, SchemaAdminService

router = APIRouter(tags=["schema"], dependencies=[Depends(require_auth)])


@router.get("/schema/grades", response_model=GradesGetResponse)
def get_grades():
    return SchemaAdminService().get()


@router.put(
    "/schema/grades",
    response_model=GradesPutResponse,
    responses={409: {"description": "서빙 분류기를 멈추게 하는 등급체계 변경 — force 와 사유 없이는 거부"}},
)
def put_grades(req: GradesPutRequest):
    # 이 사업의 오류 계약은 심볼릭 코드 없이 HTTP 상태로 분기한다(ICD) — 거부는 409.
    try:
        return SchemaAdminService().put(req)
    except GradeSchemaBlocked as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
