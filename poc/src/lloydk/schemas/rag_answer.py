"""RAG 답안 합성 결과 스키마."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from lloydk.schemas.classify import RagContextHit
from lloydk.schemas.common import Grade


class RagCitation(BaseModel):
    """답안에서 인용한 원천 표시."""

    source_doc: str
    chunk_id: str
    score: float
    rank: int  # 1-based


class RagAnswerUsage(BaseModel):
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int = 0
    success: bool = True


class RagAnswerResult(BaseModel):
    answer: str
    citations: list[RagCitation] = []
    grade: Optional[Grade] = None
    usage: Optional[RagAnswerUsage] = None
    warnings: list[str] = []
    deterministic_fallback: bool = False  # LLM 미사용 또는 실패 시 True


def hits_to_citations(hits: list[RagContextHit]) -> list[RagCitation]:
    return [
        RagCitation(
            source_doc=h.source_doc,
            chunk_id=h.chunk_id,
            score=float(h.score),
            rank=i + 1,
        )
        for i, h in enumerate(hits)
    ]
