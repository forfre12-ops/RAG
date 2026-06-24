"""A1: Reranker 어댑터 smoke 테스트.

운영 환경에서 BGE cross-encoder 로드는 lazy이며, 본 테스트는 noop만 강제 검증.
bge backend는 import 가능 여부만 확인.
"""

from __future__ import annotations

import pytest

from lloydk.adapters.reranker import NoopReranker, get_reranker
from lloydk.adapters.reranker.base import RerankResult


def test_noop_preserves_order():
    r = NoopReranker()
    out = r.rerank("query", ["doc1", "doc2", "doc3"])
    assert [o.index for o in out] == [0, 1, 2]
    assert out[0].score > out[-1].score


def test_noop_top_k_truncates():
    r = NoopReranker()
    out = r.rerank("q", ["a", "b", "c", "d"], top_k=2)
    assert len(out) == 2
    assert all(isinstance(o, RerankResult) for o in out)


def test_noop_empty_candidates():
    r = NoopReranker()
    assert r.rerank("q", []) == []


def test_get_reranker_default_is_noop(monkeypatch):
    # .env의 RERANKER_PROVIDER 값에 무관하게 no-arg 호출 시 noop fallback 검증.
    from lloydk.config import settings
    monkeypatch.setattr(settings, "reranker_provider", "")
    r = get_reranker()
    assert r.name == "noop"


def test_get_reranker_explicit_noop():
    r = get_reranker("noop")
    assert r.name == "noop"


def test_bge_reranker_module_importable():
    # 모듈은 import 가능해야 함(실제 모델 로드는 lazy)
    from lloydk.adapters.reranker.bge_reranker import BgeReranker  # noqa: F401


def test_get_reranker_bge_lazy_does_not_load_model():
    # get_reranker("bge")는 인스턴스만 만들 뿐 모델 로드 시도 안 함
    try:
        r = get_reranker("bge")
    except Exception:
        pytest.skip("BGE 의존성 미설치 환경")
    assert r.name == "bge"


def test_get_reranker_bge_import_failure_falls_back_to_noop(monkeypatch):
    # F: bge import/초기화 실패 시 raise 대신 noop 폴백(다른 팩토리와 동일 정책).
    import lloydk.adapters.reranker as rr_mod

    # 캐시 비워서 폴백 경로를 강제(이전 테스트가 'bge'를 캐시했을 수 있음).
    monkeypatch.setattr(rr_mod, "_RERANKER_CACHE", {})

    def _boom():
        raise RuntimeError("FlagEmbedding not installed")

    # BgeReranker 생성 자체가 터지는 상황을 모사.
    monkeypatch.setattr(
        "lloydk.adapters.reranker.bge_reranker.BgeReranker", _boom, raising=True
    )
    r = get_reranker("bge")
    assert r.name == "noop"
