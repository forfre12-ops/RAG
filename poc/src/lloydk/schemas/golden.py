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
        pattern=r"^(anthropic|openai|google|vllm_qwen|vllm_exaone|local_openai|noop)$",
    )
    min_rule_conf: float = Field(default=0.5, ge=0.0, le=1.0)
    min_llm_conf: float = Field(default=0.7, ge=0.0, le=1.0)
    holdout_path: Optional[str] = None                       # 누출 차단용 홀드아웃 jsonl
    out_dir: str = Field(default="datasets/gold_real")       # run-스코프 후보 출력 위치
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
