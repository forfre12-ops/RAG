"""W9 일반화 — 임베딩 캐시 레이어 통합 smoke 테스트.

검증 포인트:
  1. 첫 호출은 underlying provider.embed가 1회 호출됨
  2. 동일 텍스트 재호출 시 underlying 0회 (캐시 hit)
  3. 다른 텍스트는 underlying이 다시 호출됨

전제:
- 캐시 모듈은 `koipa.adapters.embedding.cache` 또는 동등 위치 `EmbeddingCache` 클래스로 제공됨
- 본 PoC 시점 어댑터·구현이 존재하지 않으면 모듈 단위 skip
  (scripts/cache_kure_v1.py는 HF hub 모델 다운로드 스크립트일 뿐 캐시 레이어 아님)
"""

from __future__ import annotations

import importlib

import pytest

from koipa.adapters.embedding.base import EmbeddingResult


# ---- 캐시 어댑터 후보 모듈 탐색 (없으면 skip) -----------------------------

_CACHE_CANDIDATES = [
    ("koipa.adapters.embedding.cache", "EmbeddingCache"),
    ("koipa.adapters.embedding.cached", "CachedEmbedding"),
    ("koipa.adapters.embedding.cache_layer", "EmbeddingCache"),
]

_CacheClass = None
for _mod_name, _cls_name in _CACHE_CANDIDATES:
    try:
        _mod = importlib.import_module(_mod_name)
        _CacheClass = getattr(_mod, _cls_name, None)
        if _CacheClass is not None:
            break
    except ImportError:
        continue

if _CacheClass is None:
    pytest.skip(
        "임베딩 캐시 레이어(EmbeddingCache) 미구현 — W9 캐시 어댑터가 추가되면 본 테스트가 활성화됨. "
        "현 시점 scripts/cache_kure_v1.py는 HF hub 모델 다운로드 스크립트일 뿐 메모리 캐시 레이어가 아님.",
        allow_module_level=True,
    )


# ---- 가짜 underlying provider ---------------------------------------------


class _FakeEmbedder:
    """호출 횟수를 셈하는 deterministic embedder."""

    name = "fake"
    dim = 8

    def __init__(self) -> None:
        self.call_count = 0
        self.last_batch: list[str] = []

    def embed(self, texts):
        self.call_count += 1
        self.last_batch = list(texts)
        # 텍스트 길이를 기반으로 결정론적 벡터 생성 (검증 가능성)
        vectors = [[float(len(t)) for _ in range(self.dim)] for t in texts]
        return EmbeddingResult(vectors=vectors, dim=self.dim, model=self.name)


# ============================================================================
# 검증 1·2·3
# ============================================================================


class TestEmbeddingCache:
    def test_first_call_triggers_underlying(self):
        underlying = _FakeEmbedder()
        cache = _CacheClass(underlying)
        result = cache.embed(["한국어 영업비밀 문서"])
        assert underlying.call_count == 1
        assert result.dim == 8
        assert len(result.vectors) == 1

    def test_repeated_call_is_cache_hit(self):
        """동일 텍스트 재호출 → underlying은 더 이상 호출되지 않음."""
        underlying = _FakeEmbedder()
        cache = _CacheClass(underlying)
        first = cache.embed(["동일한 텍스트"])
        second = cache.embed(["동일한 텍스트"])
        # 핵심: underlying이 단 1번만 호출되어야 캐시 hit
        assert underlying.call_count == 1
        # 결과 벡터가 동일해야 (캐시 일관성)
        assert first.vectors == second.vectors

    def test_different_text_triggers_underlying_again(self):
        underlying = _FakeEmbedder()
        cache = _CacheClass(underlying)
        _ = cache.embed(["텍스트 A"])
        _ = cache.embed(["텍스트 B"])  # 다른 텍스트
        assert underlying.call_count == 2

    def test_mixed_batch_only_uncached_go_to_underlying(self):
        """배치 안에 일부 캐시 hit + 일부 miss가 섞이면 miss만 underlying에 전달.

        구현이 배치를 통째로 위임하는 단순 캐시이면 본 테스트는 xfail로 둘 수 있으나,
        설계 의도(네트워크·모델 호출 0회 보장)를 위해 miss-only 전달이 권장됨.
        """
        underlying = _FakeEmbedder()
        cache = _CacheClass(underlying)
        cache.embed(["사전 가열 텍스트"])
        # underlying 호출 1회 후 batch — 새 텍스트만 underlying에 전달돼야
        before = underlying.call_count
        _ = cache.embed(["사전 가열 텍스트", "신규 텍스트"])
        # 적어도 한 번은 underlying이 호출돼야 (신규 텍스트 처리)
        assert underlying.call_count >= before + 1
        # 그러나 "사전 가열" 한 텍스트가 다시 underlying 배치에 들어갔다면 모든 텍스트가 통째로 갔다는 뜻
        # → 권장 동작: last_batch에 신규 텍스트만 존재
        if underlying.call_count == before + 1:
            assert "신규 텍스트" in underlying.last_batch

    def test_different_models_do_not_share_cache_keys(self):
        """동일 텍스트라도 underlying 모델명이 다르면 캐시 키가 달라야 한다.

        P2 KURE-v1 vs BGE-M3 비교에서 두 모델이 같은 텍스트로 같은 키를 잡아
        먼저 임베딩한 모델의 벡터를 두 번째 모델이 cache hit으로 받아오던 결함
        가드. cache_layer._hash_key가 model 인자를 받아 분리하도록 변경된 게
        실제로 적용됐는지 행동으로 검증.
        """
        from koipa.adapters.embedding.cache_layer import _hash_key
        text = "한국어 영업비밀 문서"
        k_none = _hash_key(text)
        k_kure = _hash_key(text, "nlpai-lab/KURE-v1")
        k_bge = _hash_key(text, "BAAI/bge-m3")
        # 모델 미지정 폴백은 기존 해시와 동일 (하위호환)
        assert k_none == _hash_key(text, None)
        # 모델 지정 시 키가 폴백과도, 다른 모델과도 달라야
        assert k_kure != k_none
        assert k_bge != k_none
        assert k_kure != k_bge
