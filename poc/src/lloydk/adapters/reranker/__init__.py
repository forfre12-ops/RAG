"""Reranker 어댑터 — A1.

Retriever 1차 결과(top-K, K≫k)를 cross-encoder로 재정렬하여 최종 top-k 반환.

기본은 noop(원본 순서 유지). BGE-reranker는 lazy import + extras `[reranker]`.
"""

from lloydk.adapters.reranker.base import Reranker, RerankResult
from lloydk.adapters.reranker.noop_reranker import NoopReranker

__all__ = ["Reranker", "RerankResult", "NoopReranker", "get_reranker"]

_RERANKER_CACHE: dict[str, Reranker] = {}


def get_reranker(provider: str | None = None) -> Reranker:
    """settings.reranker_provider 또는 명시 인자로 백엔드 선택.

    프로세스 내 싱글톤 — 모델 로드 비용(~수초)을 최초 1회로 한정.
    """
    from lloydk.config import settings

    name = (provider or getattr(settings, "reranker_provider", "noop") or "noop").lower()

    if name in _RERANKER_CACHE:
        return _RERANKER_CACHE[name]

    if name == "noop":
        instance: Reranker = NoopReranker()
    elif name == "bge":
        from lloydk.adapters.reranker.bge_reranker import BgeReranker
        instance = BgeReranker()
    else:
        instance = NoopReranker()

    _RERANKER_CACHE[name] = instance
    return instance
