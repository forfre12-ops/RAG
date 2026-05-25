"""Embedding Provider — KURE-v1 (기본) / BGE-M3 (폴백) / Hash (드라이런)."""

from __future__ import annotations

from lloydk.adapters.embedding.base import EmbeddingProvider, EmbeddingResult
from lloydk.adapters.embedding.hash_embedding import HashEmbedding

__all__ = ["EmbeddingProvider", "EmbeddingResult", "HashEmbedding", "build_embedder"]


def build_embedder(model_name: str | None = None, *, force_hash: bool = False) -> EmbeddingProvider:
    """기본은 HuggingFace 로드. 드라이런/오프라인이면 HashEmbedding."""
    from lloydk.config import settings

    name = model_name or settings.embedding_model
    if force_hash or name == "hash":
        return HashEmbedding(dim=1024)
    try:
        from lloydk.adapters.embedding.hf_embedding import HFEmbedding

        return HFEmbedding(name)
    except Exception as exc:  # noqa: BLE001
        # 모델 로드 실패(네트워크/디스크/CUDA) 시 HashEmbedding으로 폴백 + 경고
        import warnings

        warnings.warn(
            f"[embedding] HF load failed for {name}: {exc}. Falling back to HashEmbedding.",
            RuntimeWarning,
            stacklevel=2,
        )
        return HashEmbedding(dim=1024)
