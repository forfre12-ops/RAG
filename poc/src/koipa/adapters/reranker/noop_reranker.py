"""NoopReranker — 입력 순서 유지 (결정론적).

retriever가 이미 적절한 순서를 반환하는 경우 또는 reranker 미설치 환경에서 사용.
"""

from __future__ import annotations

from typing import Sequence

from koipa.adapters.reranker.base import RerankResult


class NoopReranker:
    name = "noop"

    def rerank(
        self,
        query: str,
        candidates: Sequence[str],
        *,
        top_k: int | None = None,
    ) -> list[RerankResult]:
        # score는 (1 - i/N) 로 인덱스에 단조 감소
        n = len(candidates)
        if n == 0:
            return []
        out = [
            RerankResult(index=i, score=1.0 - (i / n), document=doc)
            for i, doc in enumerate(candidates)
        ]
        if top_k is not None:
            out = out[:top_k]
        return out
