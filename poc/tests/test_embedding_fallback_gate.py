"""[require_real_embedder] HF 임베더 로드 실패 시 HashEmbedding 무음 폴백 정책 게이트.

기본(False): 폴백 허용(dryrun/lite tier 동작 보존). onprem-local·full-train 프로파일(True):
폴백 거부(fail-secure) — 검색품질 급락(정확도 저하)을 무음으로 흘리지 않고 명시적 실패.
명시적 hash 요청(force_hash/embedding_model="hash")은 '폴백'이 아니므로 항상 허용(dryrun 탈출구).
"""
from __future__ import annotations

import pytest

import lloydk.adapters.embedding as emb
from lloydk.adapters.embedding.hash_embedding import HashEmbedding


@pytest.fixture
def _break_hf(monkeypatch):
    """HFEmbedding 로드가 항상 실패하도록(네트워크/디스크/CUDA 장애 시뮬)."""
    import lloydk.adapters.embedding.hf_embedding as hf

    def _boom(*a, **k):
        raise RuntimeError("HF load failed (simulated)")

    monkeypatch.setattr(hf, "HFEmbedding", _boom)
    emb._EMBEDDER_CACHE.clear()
    yield
    emb._EMBEDDER_CACHE.clear()


def test_fallback_allowed_by_default(_break_hf, monkeypatch):
    from lloydk.config import settings

    monkeypatch.setattr(settings, "require_real_embedder", False, raising=False)
    with pytest.warns(RuntimeWarning):
        prov = emb.build_embedder("real-model-alpha")
    assert isinstance(prov, HashEmbedding)  # 무음 폴백(기본 동작 보존)


def test_fallback_refused_when_required(_break_hf, monkeypatch):
    from lloydk.config import settings

    monkeypatch.setattr(settings, "require_real_embedder", True, raising=False)
    with pytest.raises(RuntimeError, match="require_real_embedder"):
        emb.build_embedder("real-model-beta")  # 폴백 거부 = 명시적 실패(fail-secure)


def test_explicit_hash_allowed_even_when_required(monkeypatch):
    # 명시적 hash 요청은 '폴백'이 아니라 의도된 dryrun → require_real_embedder=True여도 허용.
    from lloydk.config import settings

    monkeypatch.setattr(settings, "require_real_embedder", True, raising=False)
    assert isinstance(emb.build_embedder(force_hash=True), HashEmbedding)
    assert isinstance(emb.build_embedder("hash"), HashEmbedding)


# ── startup fail-clear: warmup 이 require_real_embedder 실패를 삼키지 않음 ──────────


def _warmup_settings(**over):
    from types import SimpleNamespace

    base = dict(poc_mode="full", require_real_embedder=False,
                embedding_provider="hf", reranker_provider="noop")
    base.update(over)
    return SimpleNamespace(**base)


def test_warmup_reraises_when_real_embedder_required(monkeypatch):
    """require_real_embedder=True 하드닝 서빙: 임베더 warmup 실패는 startup fail-clear(re-raise).

    lazy(첫 요청)까지 지연하지 않고 startup 에서 걸린다 — 형제 게이트(require_safety_gates)와 대칭."""
    from lloydk.api import app as app_mod
    import lloydk.adapters.embedding as emb_mod

    def _boom(*a, **k):
        raise RuntimeError("real embedder required but HF load failed (simulated)")

    monkeypatch.setattr(emb_mod, "build_embedder", _boom)
    with pytest.raises(RuntimeError, match="require_real_embedder"):
        app_mod._warmup_models(_warmup_settings(require_real_embedder=True))


def test_warmup_swallows_when_not_required(monkeypatch):
    """require_real_embedder=False(기본): warmup 실패는 best-effort 스킵(부팅 안 막음 — 동작 보존)."""
    from lloydk.api import app as app_mod
    import lloydk.adapters.embedding as emb_mod

    def _boom(*a, **k):
        raise RuntimeError("HF load failed (simulated)")

    monkeypatch.setattr(emb_mod, "build_embedder", _boom)
    app_mod._warmup_models(_warmup_settings(require_real_embedder=False))  # no raise
