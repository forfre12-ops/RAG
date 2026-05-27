"""Classify async/batch/job 스키마 (OpenAPI /classify/async·/classify/batch·/classify/jobs)."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel

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
    job_id: UUID
    total: int
    status: str = "queued"
    status_url: str


class ClassifyJobStatus(BaseModel):
    job_id: UUID
    status: str  # queued/running/done/failed
    total: Optional[int] = None
    completed: Optional[int] = None
    results: Optional[list[ClassifyResponse]] = None
    error: Optional[str] = None
