"""Reranker 어댑터 — A1.

Retriever 1차 결과(top-K, K≫k)를 cross-encoder로 재정렬하여 최종 top-k 반환.

기본은 noop(원본 순서 유지). BGE-reranker는 lazy import + extras `[reranker]`.
"""

from lloydk.adapters.reranker.base import Reranker, RerankResult
from lloydk.adapters.reranker.noop_reranker import NoopReranker

__all__ = ["Reranker", "RerankResult", "NoopReranker", "get_reranker"]


def get_reranker(provider: str | None = None) -> Reranker:
    """settings.reranker_provider 또는 명시 인자로 백엔드 선택.

    Returns:
        Reranker 인스턴스
    """
    from lloydk.config import settings

    name = (provider or getattr(settings, "reranker_provider", "noop") or "noop").lower()

    if name == "noop":
        return NoopReranker()
    if name == "bge":
        from lloydk.adapters.reranker.bge_reranker import BgeReranker
        return BgeReranker()
    if name == "qwen3":
        from lloydk.adapters.reranker.bge_reranker import BgeReranker
        return BgeReranker(model_name="Qwen/Qwen3-Reranker-0.6B")

    return NoopReranker()
