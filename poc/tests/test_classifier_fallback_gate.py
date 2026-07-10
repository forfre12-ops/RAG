"""[require_real_classifier] 분류기 모델 로드 실패 시 rule-fallback-v0 무음 열화 게이트.

형제 게이트 require_real_embedder 와 대칭. 기본(False): 모델 dir 로드 실패 시 rule-fallback 허용
(dryrun/lite·무실데이터 테스트 동작 보존). onprem-local·full-train 프로파일(True): 모델 dir 이
해석됐는데 가중치 로드 실패면 startup fail-clear — 미보정 과신·고등급 미탐 위험을 무음으로
흘리지 않는다. 모델 dir 미해석(rule-fallback 의도)은 대상 아님(로드-실패 폴백 경로에서만 작동).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def _warmup_settings(**over):
    # embedder 블록은 best-effort(require_real_embedder=False)라 삼켜지고, reranker=noop 로 스킵된다
    # → 분류기 게이트만 독립 검증.
    base = dict(
        poc_mode="full",
        require_real_embedder=False,
        require_real_classifier=False,
        embedding_provider="hash",
        reranker_provider="noop",
    )
    base.update(over)
    return SimpleNamespace(**base)


class _FakeEmbedder:
    def embed(self, texts):  # noqa: D401
        return [[0.0]]


@pytest.fixture
def _quiet_embedder(monkeypatch):
    """warmup 의 임베더 단계를 빠르고 조용하게 — 분류기 게이트만 보이도록."""
    import lloydk.adapters.embedding as emb_mod

    monkeypatch.setattr(emb_mod, "build_embedder", lambda *a, **k: _FakeEmbedder())
    yield


def _patch_svc(monkeypatch, *, model_dir, loaded: bool):
    """ClassifyService.get_instance() 를 model_dir/_model 상태가 고정된 가짜로 교체."""
    import lloydk.services.classify_service as cs_mod

    inference = SimpleNamespace(model_dir=model_dir, _model=(object() if loaded else None))
    fake = SimpleNamespace(inference=inference)
    monkeypatch.setattr(cs_mod.ClassifyService, "get_instance", classmethod(lambda cls: fake))


def test_warmup_reraises_when_model_dir_resolved_but_not_loaded(_quiet_embedder, monkeypatch):
    """require_real_classifier=True + 모델 dir 해석됐는데 미로드 → startup fail-clear(re-raise)."""
    from lloydk.api import app as app_mod

    _patch_svc(monkeypatch, model_dir=Path("/models/v-abc"), loaded=False)
    with pytest.raises(RuntimeError, match="require_real_classifier"):
        app_mod._warmup_models(_warmup_settings(require_real_classifier=True))


def test_warmup_ok_when_model_loaded(_quiet_embedder, monkeypatch):
    """require_real_classifier=True + 모델 정상 로드 → 통과(부팅 안 막음)."""
    from lloydk.api import app as app_mod

    _patch_svc(monkeypatch, model_dir=Path("/models/v-abc"), loaded=True)
    app_mod._warmup_models(_warmup_settings(require_real_classifier=True))  # no raise


def test_warmup_ok_when_no_model_dir_is_rule_fallback_intent(_quiet_embedder, monkeypatch):
    """모델 dir 미해석(None)은 rule-fallback 의도 → require_real_classifier=True 여도 통과.

    (require_real_embedder 의 '명시적 hash 요청은 폴백 아님' 과 동일 철학 — 로드-실패만 차단.)"""
    from lloydk.api import app as app_mod

    _patch_svc(monkeypatch, model_dir=None, loaded=False)
    app_mod._warmup_models(_warmup_settings(require_real_classifier=True))  # no raise


def test_warmup_swallows_when_not_required(_quiet_embedder, monkeypatch):
    """require_real_classifier=False(기본): 미로드여도 rule-fallback 허용(동작 보존, 부팅 안 막음)."""
    from lloydk.api import app as app_mod

    _patch_svc(monkeypatch, model_dir=Path("/models/v-abc"), loaded=False)
    app_mod._warmup_models(_warmup_settings(require_real_classifier=False))  # no raise
