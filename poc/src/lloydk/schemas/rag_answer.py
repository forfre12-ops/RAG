"""RAG 답안 합성 요청·결과 스키마."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from lloydk.schemas.classify import RagContextHit
from lloydk.schemas.common import Grade


class RagAnswerRequest(BaseModel):
    """POST /answer 요청 — 질의 + 선택적 RAG context 옵션."""

    query: str = Field(min_length=1, max_length=4000)
    namespace: Optional[str] = None  # RAG collection
    top_k: int = Field(default=5, ge=1, le=20)
    grade: Optional[Grade] = None  # 분류 결과를 별도 단계에서 받아 본 호출에 전달 가능
    use_reranker: bool = True
    metadata: Optional[dict] = None  # 추가 filter용 (tenant 제거 — 격리는 KL 포털 전담)


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
