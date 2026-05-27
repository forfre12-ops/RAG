"""Embedding Provider — KURE-v1 (기본) / BGE-M3 (폴백) / Hash (드라이런).

build_embedder() 는 underlying provider 생성 직후 ``CachedEmbedding`` 으로 wrap 하여
동일 텍스트 반복 호출 시 underlying 호출 0회를 보장한다 (L1 = CachedEmbedding LRU).

- 환경변수 ``EMB_CACHE_ENABLED`` (기본 ``1``): ``0``/``false``/``no`` 면 wrap 생략
- 환경변수 ``EMB_CACHE_SIZE``       (기본 1024): LRU 용량 (cache_layer 가 직접 해석)
- HashEmbedding 은 자체가 결정론적 해시 — 캐시 wrap 의 이득이 없으므로 skip
"""

from __future__ import annotations

import os

from lloydk.adapters.embedding.base import EmbeddingProvider, EmbeddingResult
from lloydk.adapters.embedding.cache_layer import CachedEmbedding, EmbeddingCache
from lloydk.adapters.embedding.hash_embedding import HashEmbedding

__all__ = [
    "EmbeddingProvider",
    "EmbeddingResult",
    "HashEmbedding",
    "CachedEmbedding",
    "EmbeddingCache",
    "build_embedder",
]


def _cache_enabled() -> bool:
    raw = os.getenv("EMB_CACHE_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off", ""}


def _maybe_wrap_cache(provider: EmbeddingProvider) -> EmbeddingProvider:
    """HashEmbedding 은 캐시 의미 없음(이미 결정론적 해시). 그 외는 LRU wrap."""
    if isinstance(provider, HashEmbedding):
        return provider
    if not _cache_enabled():
        return provider
    return CachedEmbedding(provider)


def build_embedder(model_name: str | None = None, *, force_hash: bool = False) -> EmbeddingProvider:
    """기본은 HuggingFace 로드. 드라이런/오프라인이면 HashEmbedding.

    underlying provider 생성 직후 ``EMB_CACHE_ENABLED`` 기본 ON 이면
    ``CachedEmbedding`` 으로 wrap 하여 반환한다.
    """
    from lloydk.config import settings

    name = model_name or settings.embedding_model
    if force_hash or name == "hash":
        return HashEmbedding(dim=1024)
    try:
        from lloydk.adapters.embedding.hf_embedding import HFEmbedding

        return _maybe_wrap_cache(HFEmbedding(name))
    except Exception as exc:  # noqa: BLE001
        # 모델 로드 실패(네트워크/디스크/CUDA) 시 HashEmbedding으로 폴백 + 경고.
        # L2: Prometheus counter 증가로 운영 대시보드에서 정확도 저하 위험 가시화.
        import logging as _logging
        import warnings

        warnings.warn(
            f"[embedding] HF load failed for {name}: {exc}. Falling back to HashEmbedding.",
            RuntimeWarning,
            stacklevel=2,
        )
        _logging.getLogger(__name__).error(
            "embedding model load failed for %s, falling back to HashEmbedding: %s",
            name, exc,
        )
        try:
            from lloydk.api.prom_metrics import EMBEDDING_FALLBACK_TOTAL  # noqa: PLC0415

            EMBEDDING_FALLBACK_TOTAL.labels(original_provider=name).inc()
        except Exception as metric_exc:  # noqa: BLE001
            _logging.getLogger(__name__).debug(
                "fallback metric increment skipped: %s", metric_exc
            )
        return HashEmbedding(dim=1024)
