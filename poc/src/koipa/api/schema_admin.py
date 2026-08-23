"""GET/PUT /schema/grades — 등급체계 조회·변경 + 재학습 필요성 응답."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from koipa.api._jwt_auth import require_auth
from koipa.schemas.schema_admin import (
    GradesGetResponse,
    GradesPutRequest,
    GradesPutResponse,
)
from koipa.services.schema_admin_service import SchemaAdminService

router = APIRouter(tags=["schema"], dependencies=[Depends(require_auth)])


@router.get("/schema/grades", response_model=GradesGetResponse)
def get_grades():
    return SchemaAdminService().get()


@router.put("/schema/grades", response_model=GradesPutResponse)
def put_grades(req: GradesPutRequest):
    return SchemaAdminService().put(req)
