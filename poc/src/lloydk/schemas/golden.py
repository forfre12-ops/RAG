"""Golden builder 도메인 스키마 (/golden/*) — G3b."""

from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from .common import Actor


class GoldenBuildRequest(BaseModel):
    source_type: str = Field(default="inline", pattern=r"^(inline|corpus)$")
    docs: list[dict] = Field(default_factory=list)          # source_type=inline
    corpus_dir: Optional[str] = None                         # source_type=corpus (jsonl 파일 또는 *.json 디렉토리)
    n: int = Field(default=200, ge=1, le=5000)
    llm_provider: str = Field(
        default="noop",
        pattern=r"^(anthropic|openai|google|gemini|vllm_qwen|vllm_exaone|local_openai|noop)$",
    )
    sensitive: bool = Field(default=False)  # True=실고객 비밀 → airgap(Qwen), 공개 클라우드 금지
    min_rule_conf: float = Field(default=0.5, ge=0.0, le=1.0)        # 레거시(게이트 미사용)
    min_llm_conf: float = Field(default=0.7, ge=0.0, le=1.0)         # 레거시(게이트 미사용)
    min_self_consistency: float = Field(default=0.67, ge=0.0, le=1.0)
    holdout_path: Optional[str] = None                       # 누출 차단용 홀드아웃 jsonl
    # run-스코프 후보 출력 위치. 정본(datasets/gold_real/classification_gold.jsonl)과 산출물이
    # 섞이지 않게 builds/ 하위로 분리(run-스코프 파일명이라 덮어쓰진 않으나 위생). 승격은
    # scripts/promote_golden_candidates.py 로 명시적 게이트 통과 후 정본에 병합.
    out_dir: str = Field(default="datasets/gold_real/builds")
    actor: Actor


class GoldenBuildResponse(BaseModel):
    golden_job_id: UUID
    status_url: str


class GoldenBuildStatus(BaseModel):
    status: str                                  # queued | running | done | failed
    stats: Optional[dict] = None
    gold_count: Optional[int] = None
    uncertain_count: Optional[int] = None
    gold_path: Optional[str] = None
    uncertain_path: Optional[str] = None
    error: Optional[str] = None
