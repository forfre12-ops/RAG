"""GET/PUT /schema/grades — 고정 4등급의 표시 메타데이터 조회·변경."""

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
    responses={409: {"description": "TS/S1/S2/S3 고정 정책을 깨는 등급 코드 집합 변경"}},
)
def put_grades(req: GradesPutRequest):
    # 이 사업의 오류 계약은 심볼릭 코드 없이 HTTP 상태로 분기한다(ICD) — 정책 위반은 409.
    try:
        return SchemaAdminService().put(req)
    except GradeSchemaBlocked as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
