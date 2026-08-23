"""[require_real_embedder] HF 임베더 로드 실패 시 HashEmbedding 무음 폴백 정책 게이트.

기본(False): 폴백 허용(dryrun/lite tier 동작 보존). onprem-local·full-train 프로파일(True):
폴백 거부(fail-secure) — 검색품질 급락(정확도 저하)을 무음으로 흘리지 않고 명시적 실패.
명시적 hash 요청(force_hash/embedding_model="hash")은 '폴백'이 아니므로 항상 허용(dryrun 탈출구).

⚠ [2026-08-23] hash 를 고르는 길이 셋이다 — force_hash · embedding_model="hash" ·
  **embedding_provider="hash"**(프로파일이 정한다. lite-noapi 가 그렇다: config.py:53).
  마지막 것이 이 게이트보다 앞서므로, "실 임베더를 쓰는 서버"를 흉내내는 시험은
  embedding_provider 를 hf 로 **명시해야 한다.** 시험 기본 프로파일이 lite-noapi 라
  명시하지 않으면 HF 로드를 시도조차 안 하고 hash 로 끝나 게이트가 발화하지 않는다
  (그 상태로는 폴백 경고·거부를 확인할 수 없다).
"""
from __future__ import annotations

import pytest

import koipa.adapters.embedding as emb
from koipa.adapters.embedding.hash_embedding import HashEmbedding


@pytest.fixture
def _break_hf(monkeypatch):
    """HFEmbedding 로드가 항상 실패하도록(네트워크/디스크/CUDA 장애 시뮬)."""
    import koipa.adapters.embedding.hf_embedding as hf

    def _boom(*a, **k):
        raise RuntimeError("HF load failed (simulated)")

    monkeypatch.setattr(hf, "HFEmbedding", _boom)
    emb._EMBEDDER_CACHE.clear()
    yield
    emb._EMBEDDER_CACHE.clear()


@pytest.fixture
def _real_provider(monkeypatch):
    """실 임베더를 쓰는 서버(onprem-local·full-train 계열)를 흉내낸다.

    이 게이트는 **HF 로드를 시도한 뒤** 실패했을 때의 정책이다. provider 가 hash 면
    시도 자체를 안 하므로(설계대로) 게이트를 확인할 수 없다 — 그래서 여기서 못 박는다.
    """
    from koipa.config import settings

    monkeypatch.setattr(settings, "embedding_provider", "hf", raising=False)
    return settings


def test_fallback_allowed_by_default(_break_hf, _real_provider, monkeypatch):
    settings = _real_provider

    monkeypatch.setattr(settings, "require_real_embedder", False, raising=False)
    with pytest.warns(RuntimeWarning):
        prov = emb.build_embedder("real-model-alpha")
    assert isinstance(prov, HashEmbedding)  # 무음 폴백(기본 동작 보존)


def test_fallback_refused_when_required(_break_hf, _real_provider, monkeypatch):
    settings = _real_provider

    monkeypatch.setattr(settings, "require_real_embedder", True, raising=False)
    with pytest.raises(RuntimeError, match="require_real_embedder"):
        emb.build_embedder("real-model-beta")  # 폴백 거부 = 명시적 실패(fail-secure)


def test_explicit_hash_allowed_even_when_required(_real_provider, monkeypatch):
    # 명시적 hash 요청은 '폴백'이 아니라 의도된 dryrun → require_real_embedder=True여도 허용.
    # provider 를 hf 로 두고 확인한다 — 안 그러면 provider 때문에 통과해 이 시험이 뜻을 잃는다.
    settings = _real_provider

    monkeypatch.setattr(settings, "require_real_embedder", True, raising=False)
    assert isinstance(emb.build_embedder(force_hash=True), HashEmbedding)
    assert isinstance(emb.build_embedder("hash"), HashEmbedding)


def test_hash_provider_never_touches_hf(_break_hf, monkeypatch):
    """provider=hash 프로파일(lite-noapi)은 모델 이름이 무엇이든 HF 를 건드리지 않는다.

    왜(2026-08-23). 종전에는 모델 이름만 봤다. 그래서 lite-noapi 가 설정으로는 hash 를
    내걸고도 실제로는 HF 를 조용히 로드했다 — 화면·헬스가 말하는 것과 도는 것이 달랐다.
    이제 provider 가 앞선다. 로드를 아예 안 하므로 폴백 경고도 나오지 않는다
    (경고는 '시도했다 실패했다'는 뜻이라, 시도조차 안 한 자리에 뜨면 그게 거짓말이다).
    """
    from koipa.config import settings

    monkeypatch.setattr(settings, "embedding_provider", "hash", raising=False)
    monkeypatch.setattr(settings, "require_real_embedder", True, raising=False)

    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)   # 경고가 나면 여기서 터진다
        prov = emb.build_embedder("nlpai-lab/KURE-v1")
    assert isinstance(prov, HashEmbedding)


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
    from koipa.api import app as app_mod
    import koipa.adapters.embedding as emb_mod

    def _boom(*a, **k):
        raise RuntimeError("real embedder required but HF load failed (simulated)")

    monkeypatch.setattr(emb_mod, "build_embedder", _boom)
    with pytest.raises(RuntimeError, match="require_real_embedder"):
        app_mod._warmup_models(_warmup_settings(require_real_embedder=True))


def test_warmup_swallows_when_not_required(monkeypatch):
    """require_real_embedder=False(기본): warmup 실패는 best-effort 스킵(부팅 안 막음 — 동작 보존)."""
    from koipa.api import app as app_mod
    import koipa.adapters.embedding as emb_mod

    def _boom(*a, **k):
        raise RuntimeError("HF load failed (simulated)")

    monkeypatch.setattr(emb_mod, "build_embedder", _boom)
    app_mod._warmup_models(_warmup_settings(require_real_embedder=False))  # no raise
