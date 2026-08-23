"""Reranker Protocol 정의."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable


@dataclass
class RerankResult:
    index: int     # 입력 candidates 내 원래 인덱스
    score: float   # cross-encoder relevance score
    document: str  # 원문 (참조용)


@runtime_checkable
class Reranker(Protocol):
    name: str

    def rerank(
        self,
        query: str,
        candidates: Sequence[str],
        *,
        top_k: int | None = None,
    ) -> list[RerankResult]: ...
