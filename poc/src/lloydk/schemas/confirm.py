"""Confirm/Relabel 도메인 스키마 (OpenAPI /confirm·/relabel)."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from .common import Actor, Grade


class ConfirmRequest(BaseModel):
    doc_id: str
    model_version: Optional[str] = None
    inference_id: Optional[UUID] = None
    confirmed_label: Grade
    actor: Actor
    note: Optional[str] = Field(default=None, max_length=1000)


class ConfirmResponse(BaseModel):
    confirmation_id: UUID
    confirmed_at: str  # ISO-8601


class RelabelRequest(BaseModel):
    doc_id: str
    inference_id: Optional[UUID] = None
    original_label: Grade
    corrected_label: Grade
    reason: Optional[str] = Field(default=None, max_length=2000)
    actor: Actor


class RelabelResponse(BaseModel):
    relabel_id: UUID
    queue_size: int = Field(description="대기 중인 미소비 corrections 수")
    retrain_threshold: int = Field(description="재학습 자동 트리거 임계")
