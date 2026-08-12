"""Reranker 어댑터 — A1.

Retriever 1차 결과(top-K, K≫k)를 cross-encoder로 재정렬하여 최종 top-k 반환.

기본은 noop(원본 순서 유지). BGE-reranker는 lazy import + extras `[reranker]`.
"""

import logging

from koipa.adapters.reranker.base import Reranker, RerankResult
from koipa.adapters.reranker.noop_reranker import NoopReranker

__all__ = ["Reranker", "RerankResult", "NoopReranker", "get_reranker"]

logger = logging.getLogger(__name__)

_RERANKER_CACHE: dict[str, Reranker] = {}


def get_reranker(provider: str | None = None) -> Reranker:
    """settings.reranker_provider 또는 명시 인자로 백엔드 선택.

    프로세스 내 싱글톤 — 모델 로드 비용(~수초)을 최초 1회로 한정.

    bge 백엔드의 import/초기화 실패는 noop으로 graceful 폴백(경고 로그) —
    vectorstore/storage 팩토리와 동일 정책. 미설치 환경에서 raise 하지 않는다.
    """
    from koipa.config import settings

    name = (provider or getattr(settings, "reranker_provider", "noop") or "noop").lower()

    if name in _RERANKER_CACHE:
        return _RERANKER_CACHE[name]

    if name == "noop":
        instance: Reranker = NoopReranker()
    elif name == "bge":
        try:
            from koipa.adapters.reranker.bge_reranker import BgeReranker
            instance = BgeReranker()
        except Exception as exc:  # noqa: BLE001 — 미설치/초기화 실패 시 noop 폴백
            logger.warning(
                "[reranker] bge backend unavailable: %s. Falling back to NoopReranker.",
                exc,
            )
            instance = NoopReranker()
    else:
        instance = NoopReranker()

    _RERANKER_CACHE[name] = instance
    return instance
