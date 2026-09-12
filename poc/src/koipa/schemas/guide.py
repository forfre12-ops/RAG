"""Guide 도메인 스키마 (OpenAPI /guide/documents)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from koipa.schemas.common import Actor


class GuideVersionRegisterRequest(BaseModel):
    """가이드 버전 등록 요청 — **파일을 받지 않는다**.

    [2026-09-05] 종전 API 는 multipart 로 파일을 필수로 받고 버렸다. 발주처 원문이 우리
    서버 메모리를 한 번 지나는 경로였고, RTM 요건도 아니다. 버전 메타만 받는다.
    """

    guide_id: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=30)
    actor: Actor
    effective_date: Optional[str] = None
    change_summary: Optional[str] = Field(default=None, max_length=2000)
    doc_type: Optional[str] = Field(default=None, max_length=50)
    # 사람이 어느 문서를 등록했는지 적는 **메타**다. 파일은 받지 않는다.
    filename: Optional[str] = Field(default=None, max_length=255)

class GuideUploadResponse(BaseModel):
    guide_id: str
    version: str
    triggers_retraining: bool


class GuideVersionItem(BaseModel):
    version: str
    effective_date: Optional[str] = None
    change_summary: Optional[str] = None
    registered_at: Optional[str] = None


class GuideVersionList(BaseModel):
    guide_id: str
    current_training_version: Optional[str] = None
    versions: list[GuideVersionItem]
