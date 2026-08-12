"""Guide 도메인 스키마 (OpenAPI /guide/documents)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class GuideUploadResponse(BaseModel):
    guide_id: str
    version: str
    indexed: bool
    embedding_vector_count: int
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
