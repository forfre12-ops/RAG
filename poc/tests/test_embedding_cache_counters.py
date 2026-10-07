"""§4: CachedEmbedding hit/miss 누적 카운터 + prom 메트릭 검증.

운영 진입 시 캐시 적중률 = 임베딩 API 비용 절감 근거. cache_info에 누적값
노출 + prom Counter 2종(koipa_embedding_cache_hit_total,
koipa_embedding_cache_miss_total)을 부작용으로 자동 갱신.
"""

from __future__ import annotations

from koipa.adapters.embedding.base import EmbeddingResult
from koipa.adapters.embedding.cache_layer import EmbeddingCache


class _StubUnderlying:
    name = "stub-emb"
    dim = 2

    def __init__(self):
        self.calls: list[list[str]] = []

    def embed(self, texts):
        ts = list(texts)
        self.calls.append(ts)
        return EmbeddingResult(
            vectors=[[float(i + 1), float(i + 2)] for i in range(len(ts))],
            dim=2,
            model=self.name,
        )


def test_counters_increment_on_miss_then_hit() -> None:
    """첫 호출은 miss, 두 번째 같은 텍스트는 lru hit. 카운터 모두 누적."""
    under = _StubUnderlying()
    cache = EmbeddingCache(under)

    info0 = cache.cache_info()
    assert info0["lru_hits"] == 0
    assert info0["misses"] == 0

    cache.embed(["a", "b"])
    info1 = cache.cache_info()
    assert info1["misses"] == 2, info1
    assert info1["lru_hits"] == 0

    cache.embed(["a", "b"])
    info2 = cache.cache_info()
    assert info2["lru_hits"] == 2, info2
    assert info2["misses"] == 2, "두 번째 호출은 추가 miss 없음"


def test_mixed_batch_counts_per_text() -> None:
    """배치 내 hit + miss 혼합 시 각각 카운트."""
    under = _StubUnderlying()
    cache = EmbeddingCache(under)
    cache.embed(["x"])  # x: miss
    cache.embed(["x", "y"])  # x: lru hit, y: miss
    info = cache.cache_info()
    assert info["lru_hits"] == 1
    assert info["misses"] == 2


def test_duplicate_in_batch_counts_as_single_miss() -> None:
    """같은 배치 내 중복 텍스트는 underlying 1회 호출. 카운터에는 1 miss."""
    under = _StubUnderlying()
    cache = EmbeddingCache(under)
    cache.embed(["dup", "dup", "dup"])
    info = cache.cache_info()
    assert info["misses"] == 1, info
    assert under.calls == [["dup"]], "underlying은 unique 텍스트만 받음"


def test_prom_counters_increment() -> None:
    """prom EMBEDDING_CACHE_HIT_TOTAL{layer=lru} + MISS_TOTAL이 증가."""
    from koipa.api import prom_metrics

    def _miss_count() -> float:
        for m in prom_metrics.EMBEDDING_CACHE_MISS_TOTAL.collect():
            for s in m.samples:
                if s.name.endswith("_total"):
                    return s.value
        return 0.0

    def _hit_count(layer: str) -> float:
        for m in prom_metrics.EMBEDDING_CACHE_HIT_TOTAL.collect():
            for s in m.samples:
                if s.name.endswith("_total") and s.labels.get("layer") == layer:
                    return s.value
        return 0.0

    miss_before = _miss_count()
    hit_before = _hit_count("lru")

    under = _StubUnderlying()
    cache = EmbeddingCache(under)
    cache.embed(["p1", "p2"])  # 2 miss
    cache.embed(["p1"])  # 1 lru hit

    assert _miss_count() >= miss_before + 2
    assert _hit_count("lru") >= hit_before + 1
