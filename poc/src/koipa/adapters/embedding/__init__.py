"""Embedding Provider — KURE-v1 (기본) / BGE-M3 (폴백) / Hash (드라이런).

build_embedder() 는 underlying provider 생성 직후 ``CachedEmbedding`` 으로 wrap 하여
동일 텍스트 반복 호출 시 underlying 호출 0회를 보장한다 (L1 = CachedEmbedding LRU).

- 환경변수 ``EMB_CACHE_ENABLED`` (기본 ``1``): ``0``/``false``/``no`` 면 wrap 생략
- 환경변수 ``EMB_CACHE_SIZE``       (기본 1024): LRU 용량 (cache_layer 가 직접 해석)
- HashEmbedding 은 자체가 결정론적 해시 — 캐시 wrap 의 이득이 없으므로 skip
"""

from __future__ import annotations

import os

from koipa.adapters.embedding.base import EmbeddingProvider, EmbeddingResult
from koipa.adapters.embedding.cache_layer import CachedEmbedding, EmbeddingCache
from koipa.adapters.embedding.hash_embedding import HashEmbedding

__all__ = [
    "EmbeddingProvider",
    "EmbeddingResult",
    "HashEmbedding",
    "CachedEmbedding",
    "EmbeddingCache",
    "build_embedder",
    "embedder_digest",
]


def embedder_digest(
    provider: EmbeddingProvider, *, requested: str | None = None
) -> dict[str, object]:
    """이 산출물을 어느 임베더가 만들었나 — 결과 파일에 그대로 실을 한 덩어리.

    실서빙은 ``require_real_embedder`` 가 기동을 거부하므로(api/app.py) HashEmbedding
    무음 폴백이 남아 있는 곳은 **평가·학습 스크립트 경로**뿐이다. 거기서는 즉시 실패보다
    이쪽이 실질적이다 — 폐쇄망·오프라인에서 해시로 돌려 보는 것 자체는 정당한 작업이고,
    문제는 그렇게 나온 숫자가 나중에 실 임베더 숫자와 구분되지 않는 것이다.

    ``degraded=True`` 는 **실 모델을 요청했는데 해시가 돌아온** 경우다. 그 숫자는 검색
    품질 근거로 인용하면 안 된다(HashEmbedding = 의미 없는 결정론적 해시).
    ``requested`` 를 안 주면 설정값(settings.embedding_model)을 기준으로 판정한다.
    """
    try:
        from koipa.config import settings  # noqa: PLC0415
        configured = str(getattr(settings, "embedding_model", "") or "")
    except Exception:  # noqa: BLE001
        configured = ""
    asked = str(requested or configured or "")
    # CachedEmbedding 으로 wrap 되면 name 이 "cached" 라 모델 구분이 사라진다.
    effective = str(getattr(provider, "_underlying_name", None) or getattr(provider, "name", "unknown"))
    return {
        "requested": asked or None,
        "effective": effective,
        "dim": int(getattr(provider, "dim", 0) or 0),
        "cached": bool(getattr(provider, "_underlying_name", None)),
        # 명시적 hash 요청은 열화가 아니다 — 의도한 드라이런이다.
        "degraded": bool(effective == "hash" and asked not in {"", "hash"}),
        "provider_class": type(provider).__name__,
    }


def _cache_enabled() -> bool:
    raw = os.getenv("EMB_CACHE_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off", ""}


def _redis_url_for_emb_cache() -> str | None:
    """표적 3 (2026-05-29): CachedEmbedding에 Redis 영속 백엔드 자동 주입.

    우선순위:
      1. env ``EMB_REDIS_URL`` 명시값 (캐시 전용 redis 분리 가능)
      2. env ``EMB_REDIS_ENABLED=0|false`` → None (강제 비활성)
      3. settings.redis_url (broker와 공유, 운영 기본 경로)
      4. 로드 실패 → None (LRU만 동작)
    """
    explicit = os.getenv("EMB_REDIS_URL", "").strip()
    if explicit:
        return explicit
    if os.getenv("EMB_REDIS_ENABLED", "1").strip().lower() in {"0", "false", "no", "off"}:
        return None
    try:
        from koipa.config import settings  # noqa: PLC0415
        return settings.redis_url or None
    except Exception:  # noqa: BLE001
        return None


def _maybe_wrap_cache(provider: EmbeddingProvider) -> EmbeddingProvider:
    """HashEmbedding 은 캐시 의미 없음(이미 결정론적 해시). 그 외는 LRU + Redis wrap.

    Redis URL은 환경/settings에서 자동 해석. redis 패키지 미설치 또는 연결 실패 시
    LRU만 동작 (CachedEmbedding._RedisCache가 silent skip).
    """
    if isinstance(provider, HashEmbedding):
        return provider
    if not _cache_enabled():
        return provider
    return CachedEmbedding(provider, redis_url=_redis_url_for_emb_cache())


_EMBEDDER_CACHE: dict[str, EmbeddingProvider] = {}


def build_embedder(model_name: str | None = None, *, force_hash: bool = False) -> EmbeddingProvider:
    """기본은 HuggingFace 로드. 드라이런/오프라인이면 HashEmbedding.

    프로세스 내 싱글톤 — HF 모델 로드 비용을 최초 1회로 한정.
    underlying provider 생성 직후 ``EMB_CACHE_ENABLED`` 기본 ON 이면
    ``CachedEmbedding`` 으로 wrap 하여 반환한다.
    """
    from koipa.config import settings

    name = model_name or settings.embedding_model
    if force_hash or name == "hash":
        return HashEmbedding(dim=1024)

    if name in _EMBEDDER_CACHE:
        return _EMBEDDER_CACHE[name]

    try:
        from koipa.adapters.embedding.hf_embedding import HFEmbedding

        provider = _maybe_wrap_cache(HFEmbedding(name))
        _EMBEDDER_CACHE[name] = provider
        return provider
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
            from koipa.api.prom_metrics import EMBEDDING_FALLBACK_TOTAL  # noqa: PLC0415

            EMBEDDING_FALLBACK_TOTAL.labels(original_provider=name).inc()
        except Exception as metric_exc:  # noqa: BLE001
            _logging.getLogger(__name__).debug(
                "fallback metric increment skipped: %s", metric_exc
            )
        # [require_real_embedder] 폐쇄망 실서빙(onprem/full)은 hash 무음 폴백(검색품질 급락)을
        # 거부 — 무음 정확도 저하 대신 명시적 실패(fail-secure). 명시적 hash 요청(force_hash/
        # name=="hash")은 위에서 이미 반환됐으므로 여기 도달=진짜 임베더를 원했는데 로드 실패뿐.
        if getattr(settings, "require_real_embedder", False):
            raise RuntimeError(
                f"real embedder required but HF load failed for {name!r}: {exc}. "
                "Refusing HashEmbedding fallback (require_real_embedder=True) — "
                "fix the model/cache or explicitly request embedding_model='hash'."
            ) from exc
        return HashEmbedding(dim=1024)
