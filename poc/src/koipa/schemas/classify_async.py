"""Classify async/batch/job 스키마 (OpenAPI /classify/async·/classify/batch·/classify/jobs)."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from .classify import ClassifyJobResult, ClassifyRequest


class ClassifyAsyncRequest(ClassifyRequest):
    callback_url: Optional[str] = None
    # [2026-10-06] KL 요청 — doc_id 는 내용 해시로 재사용(dedupe)되고 job_id 는 응답을 받은 뒤에야
    # 알 수 있어, 둘 다 호출자가 보낸 "이 요청"과 "그 결과"를 미리 묶는 키가 못 된다. 호출자가
    # 지정한 임의 문자열을 그대로 작업(job)에 저장해 되돌려준다 — 서버는 의미를 해석하지 않는다.
    client_request_id: Optional[str] = None


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
    client_request_id: Optional[str] = None
    total: Optional[int] = None
    completed: Optional[int] = None
    failed: Optional[int] = None
    failed_doc_ids: list[str] = Field(default_factory=list)
    errors: list[dict] = Field(default_factory=list)
    results: Optional[list[ClassifyJobResult]] = None
    error: Optional[str] = None
