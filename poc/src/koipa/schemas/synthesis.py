"""Synthesis 도메인 스키마 (OpenAPI /synth/*)."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from .common import Actor, Grade


class SynthGenerateRequest(BaseModel):
    target_grade: Grade
    domain: str = Field(default="mixed", pattern=r"^(tech|business|hr|finance|legal|mixed)$")
    count: int = Field(ge=1, le=500)
    llm_provider: str = Field(
        default="anthropic",
        pattern=r"^(anthropic|openai|google|vllm_qwen|vllm_exaone|noop)$",
    )
    seed_documents: list[str] = Field(default_factory=list)
    actor: Actor


class SynthGenerateResponse(BaseModel):
    synth_job_id: UUID
    expected_count: int
    estimated_cost_usd: float


class SyntheticDocItem(BaseModel):
    synth_id: UUID
    target_grade: Grade
    domain: Optional[str] = None
    llm_provider: str
    llm_model: str
    quality_score: Optional[float] = None
    review_status: str
    # 검수자가 승인하면서 고친 등급. None=교정 없음(target_grade 가 그대로 학습 라벨).
    corrected_grade: Optional[Grade] = None
    preview: Optional[str] = Field(default=None, max_length=2000)
    created_at: Optional[str] = None


class SynthQueueResponse(BaseModel):
    total: int
    items: list[SyntheticDocItem]


class SynthReviewRequest(BaseModel):
    decision: str = Field(pattern=r"^(approve|reject)$")
    corrected_grade: Optional[Grade] = None
    comment: Optional[str] = Field(default=None, max_length=2000)
    actor: Actor


class SynthReviewResponse(BaseModel):
    synth_id: UUID
    final_status: str
    # 이 건이 학습행으로 만들어질 때 쓰일 등급. corrected_grade 를 보냈으면 그 값,
    # 아니면 생성 시 목표 등급. 반려면 학습에 들어가지 않으므로 참고값이다.
    applied_grade: Optional[Grade] = None
    added_to_dataset_version: Optional[str] = None
