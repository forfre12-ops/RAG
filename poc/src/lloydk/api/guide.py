"""POST /guide/documents + GET /guide/documents/{guide_id} — 가이드 문서 (FUN-002)."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from lloydk.api._jwt_auth import require_auth, resolve_effective_tenant
from lloydk.api._rbac import require_role
from lloydk.config import settings
from lloydk.schemas.common import Actor
from lloydk.schemas.guide import GuideUploadResponse, GuideVersionList
from lloydk.services.guide_service import GuideService

router = APIRouter(tags=["guide"], dependencies=[Depends(require_auth)])


# 가이드 문서 업로드(FUN-002)는 전역 기준 문서를 바꾸는 변경성 작업 →
# admin/kl_backend로 제한. 버전 조회(GET)는 인증된 사용자면 허용(tenant 스코프).
@router.post(
    "/guide/documents",
    response_model=GuideUploadResponse,
    status_code=201,
    dependencies=[Depends(require_role("admin", "kl_backend"))],
)
async def upload_guide(
    request: Request,
    guide_id: str = Form(...),
    version: str = Form(...),
    actor: str = Form(..., description="Actor JSON 문자열 (multipart 제약)"),
    effective_date: Optional[str] = Form(default=None),
    change_summary: Optional[str] = Form(default=None),
    doc_type: Optional[str] = Form(default=None),
    file: UploadFile = File(...),
):
    try:
        actor_obj = Actor.model_validate(json.loads(actor))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid actor json: {exc}") from exc
    # R3: 업로드 본문 크기 한도 검증 — OOM·DoS 차단.
    # 1차: file.size (multipart Content-Length 기반, 클라이언트 신고값).
    # 2차: read 후 실제 바이트 길이 (조작 방지).
    max_bytes = settings.max_upload_mb * 1024 * 1024
    declared_size = getattr(file, "size", None)
    if declared_size is not None and declared_size > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"file too large: {declared_size} bytes > {max_bytes} bytes ({settings.max_upload_mb}MB)",
        )
    body = await file.read()
    if len(body) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"file too large: {len(body)} bytes > {max_bytes} bytes ({settings.max_upload_mb}MB)",
        )
    return GuideService.get_instance().upload(
        guide_id=guide_id,
        version=version,
        effective_date=effective_date,
        change_summary=change_summary,
        content_bytes=body,
        actor_user_id=actor_obj.user_id,
        # 보안: 클라이언트가 보낸 actor.tenant_id를 인증 컨텍스트에 결속(위조 차단). 불일치 시 403.
        tenant_id=resolve_effective_tenant(request, actor_obj.tenant_id) or "default",
        doc_type=doc_type,
        filename=file.filename or f"{guide_id}.txt",
    )


@router.get("/guide/documents/{guide_id}", response_model=GuideVersionList)
def list_guide_versions(guide_id: str, request: Request):
    # 읽기경로도 인증 tenant로 스코프 — 업로드(쓰기)가 실제 tenant로 격리 저장하므로
    # 조회도 동일 tenant로 해야 비-default tenant 가이드가 노출된다(W2 후속).
    tenant_id = resolve_effective_tenant(request, None) or "default"
    res = GuideService.get_instance().list_versions(guide_id, tenant_id=tenant_id)
    if res is None:
        raise HTTPException(status_code=404, detail="guide_id not found")
    return res
