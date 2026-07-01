"""검증라벨 승급(promotion) 도메인 스키마 — 교정(corrections) → 검증 DocumentLabel.

교정은 고객사(enable_training=False)에서 재학습으로 흐르지 못해 사실상 '쓰기 전용'이라
서빙 정확도를 자동으로 바꾸지 않는다. 사람 검수자가 admission(사람검수+finalized) 통과 교정을
검증 DocumentLabel로 '승급'하면 서빙 경로가 그 등급을 즉시 반영(모델 스킵, exact-match)한다.
재학습·가중치 변경 없음 → 죽음의 나선과 무관(전파 없음, 폭발반경=문서 1건).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .common import Actor, Grade


class PendingPromotionItem(BaseModel):
    doc_id: str
    proposed_label: Grade = Field(description="최신 admissible 교정이 제안하는 등급")
    corrected_by: str = Field(description="등급을 교정한 사람 검수자 id")
    corrected_at: str
    reason: Optional[str] = None
    current_verified_label: Optional[Grade] = Field(
        default=None,
        description="이미 검증된 라벨이 있고 제안과 다르면 그 등급(없거나 동일이면 None)",
    )
    classification_id: str


class PendingPromotionResponse(BaseModel):
    count: int
    items: list[PendingPromotionItem]


class PromoteRequest(BaseModel):
    doc_id: str
    actor: Actor
    expected_label: Optional[Grade] = Field(
        default=None,
        description=(
            "큐에서 본 제안 등급. 지정 시 현재 최신 교정 등급과 다르면 승급을 거부한다"
            " (승급 직전 등급이 새 교정으로 바뀌는 race 가드)."
        ),
    )
    note: Optional[str] = Field(default=None, max_length=1000)


class PromoteResponse(BaseModel):
    doc_id: str
    status: str = Field(
        description="promoted | already_promoted | not_promotable | mismatch"
    )
    promoted_label: Optional[Grade] = None
    labeler_id: Optional[str] = Field(
        default=None, description="검증라벨에 기록된 등급 결정 검수자(corrected_by)"
    )
    warnings: list[str] = Field(default_factory=list)
