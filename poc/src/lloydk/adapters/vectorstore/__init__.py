"""Vector Store — Qdrant (기본) / InMemory (드라이런)."""

from __future__ import annotations

from lloydk.adapters.vectorstore.base import SearchHit, VectorStore
from lloydk.adapters.vectorstore.inmemory_store import InMemoryStore

__all__ = ["VectorStore", "SearchHit", "InMemoryStore", "build_store"]


def build_store(*, force_memory: bool = False) -> VectorStore:
    if force_memory:
        return InMemoryStore()
    try:
        from lloydk.adapters.vectorstore.qdrant_store import QdrantStore

        return QdrantStore()
    except Exception as exc:  # noqa: BLE001
        import warnings

        warnings.warn(
            f"[vectorstore] Qdrant unavailable: {exc}. Falling back to InMemoryStore.",
            RuntimeWarning,
            stacklevel=2,
        )
        return InMemoryStore()
