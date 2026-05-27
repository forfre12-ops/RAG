"""POST /guide/documents + GET /guide/documents/{guide_id} — 가이드 문서 (FUN-002)."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from lloydk.api._auth import require_api_key
from lloydk.schemas.common import Actor
from lloydk.schemas.guide import GuideUploadResponse, GuideVersionList
from lloydk.services.guide_service import GuideService

router = APIRouter(tags=["guide"], dependencies=[Depends(require_api_key)])


@router.post("/guide/documents", response_model=GuideUploadResponse, status_code=201)
async def upload_guide(
    guide_id: str = Form(...),
    version: str = Form(...),
    actor: str = Form(..., description="Actor JSON 문자열 (multipart 제약)"),
    effective_date: Optional[str] = Form(default=None),
    change_summary: Optional[str] = Form(default=None),
    file: UploadFile = File(...),
):
    try:
        actor_obj = Actor.model_validate(json.loads(actor))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid actor json: {exc}") from exc
    body = await file.read()
    return GuideService.get_instance().upload(
        guide_id=guide_id,
        version=version,
        effective_date=effective_date,
        change_summary=change_summary,
        content_bytes=body,
        actor_user_id=actor_obj.user_id,
    )


@router.get("/guide/documents/{guide_id}", response_model=GuideVersionList)
def list_guide_versions(guide_id: str):
    res = GuideService.get_instance().list_versions(guide_id)
    if res is None:
        raise HTTPException(status_code=404, detail="guide_id not found")
    return res
