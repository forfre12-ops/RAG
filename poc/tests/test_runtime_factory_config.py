"""Runtime factories must honor the already-resolved deploy profile settings."""

from __future__ import annotations

from types import SimpleNamespace

from koipa.adapters.embedding import HashEmbedding, build_embedder


def test_embedder_uses_settings_provider_not_just_model_name(monkeypatch):
    import koipa.config as config_mod

    # lite-noapi resolves this provider to hash even though it retains the
    # normal HF model name for profiles that do use Hugging Face.
    monkeypatch.setattr(
        config_mod,
        "settings",
        SimpleNamespace(embedding_provider="hash", embedding_model="nlpai-lab/KURE-v1"),
    )

    assert isinstance(build_embedder(), HashEmbedding)
