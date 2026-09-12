"""Classify async/batch/job 스키마 (OpenAPI /classify/async·/classify/batch·/classify/jobs)."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from .classify import ClassifyRequest, ClassifyResponse


class ClassifyAsyncRequest(ClassifyRequest):
    callback_url: Optional[str] = None


class ClassifyAsyncResponse(BaseModel):
    job_id: UUID
    status: str = "queued"
    status_url: str


class ClassifyBatchRequest(BaseModel):
    documents: list[ClassifyRequest]
    callback_url: Optional[str] = None


class ClassifyBatchResponse(BaseModel):
    """배치 등록 응답.

    부분 실패 처리 도입(2026-05) 이후 부터:
    - completed/failed: 인-라인 처리 시점의 즉시 카운트 (PoC는 즉시 실행).
    - failed_doc_ids/errors: 영구 실패한 건의 doc_id·사유 로그.
    """

    job_id: UUID
    total: int
    status: str = "queued"
    status_url: str
    completed: int = 0
    failed: int = 0
    failed_doc_ids: list[str] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)


class ClassifyJobStatus(BaseModel):
    job_id: UUID
    status: str  # queued/running/done/failed/partial
    total: Optional[int] = None
    completed: Optional[int] = None
    failed: Optional[int] = None
    failed_doc_ids: list[str] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)
    results: Optional[list[ClassifyResponse]] = None
    error: Optional[str] = None
