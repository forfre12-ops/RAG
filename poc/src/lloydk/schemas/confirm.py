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
    # 정직성(P1): 서비스는 persisted/warnings 를 판정하는데 응답이 버려 '무음 성공'(분류 미존재
    # /DB미가용에도 200+confirmation_id)이었다. HTTP status 는 DB-down 안전계약상 200 유지하되,
    # 아래 필드로 실제 영속 여부·경고를 표면화해 호출자가 감사-only 상황을 구분하게 한다.
    persisted: bool = Field(
        default=True,
        description="DB 영속 여부. False=분류 미존재/DB미가용으로 감사로만 기록(무음 성공 아님).",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="비치명 경고(분류 미존재·미지정 라벨·멱등 중복·영속 실패 등).",
    )
    second_review_required: bool = Field(
        default=False,
        description="고등급 2인검토 게이트로 needs_second_review 보류됐는지.",
    )


# ── 검수 큐 조회 (GET /review-queue) — FUN-024 승인 대기 목록 ──────────────────
class ReviewQueueItem(BaseModel):
    classification_id: str
    doc_id: str
    filename: Optional[str] = None
    grade: str                       # level_code (TS/S1/S2/S3) — 모델 예측 등급
    confidence: float
    model_version: str
    status: str                      # needs_review | needs_second_review
    classified_at: Optional[str] = None  # ISO-8601
    text_preview: Optional[str] = None


class ReviewQueueResponse(BaseModel):
    items: list[ReviewQueueItem] = Field(default_factory=list)
    total: int = Field(default=0, description="필터에 해당하는 전체 대기 건수(페이지네이션 전).")
    limit: int
    offset: int
    warnings: list[str] = Field(
        default_factory=list,
        description="DB 미가용 등 비치명 경고. 있으면 빈 items 가 '대기 0'이 아니라 '조회 실패'임을 구분.",
    )


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
    # 정직성(P1): confirm 과 동일 — persisted/warnings 표면화로 '무음 성공' 방지.
    persisted: bool = Field(
        default=True,
        description="DB 영속 여부. False=분류 미존재/DB미가용으로 감사로만 기록(무음 성공 아님).",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="비치명 경고(분류 미존재·미지정 라벨·멱등 중복·영속 실패 등).",
    )
    second_review_required: bool = Field(
        default=False,
        description="고등급 2인검토 게이트로 needs_second_review 보류됐는지.",
    )
