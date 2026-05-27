"""Vector Store — Elasticsearch(기본) / pgvector(폴백) / Qdrant(롤백) / InMemory(dryrun).

doc/13_벡터DB_ES_전환_계획서.md §8.1 build_store 시그니처.

backend 결정 우선순위:
  1. 명시적 인자 `backend=...`
  2. 환경변수 `VECTOR_BACKEND`
  3. 기본값 "es"

`force_memory=True`는 테스트용 강제 inmemory.
"""

from __future__ import annotations

import os
import warnings

from lloydk.adapters.vectorstore.base import SearchHit, VectorStore
from lloydk.adapters.vectorstore.inmemory_store import InMemoryStore

__all__ = ["VectorStore", "SearchHit", "InMemoryStore", "build_store"]


def build_store(
    *,
    backend: str | None = None,
    force_memory: bool = False,
) -> VectorStore:
    if force_memory:
        return InMemoryStore()

    backend = (backend or os.getenv("VECTOR_BACKEND", "es")).lower()

    if backend == "inmemory":
        return InMemoryStore()

    if backend == "es":
        try:
            from lloydk.adapters.vectorstore.es_store import EsStore  # noqa: PLC0415

            return EsStore()
        except Exception as exc:  # noqa: BLE001
            warnings.warn(
                f"[vectorstore] Elasticsearch unavailable: {exc}. Falling back to InMemoryStore.",
                RuntimeWarning,
                stacklevel=2,
            )
            return InMemoryStore()

    if backend == "qdrant":
        try:
            from lloydk.adapters.vectorstore.qdrant_store import QdrantStore  # noqa: PLC0415

            return QdrantStore()
        except Exception as exc:  # noqa: BLE001
            warnings.warn(
                f"[vectorstore] Qdrant unavailable: {exc}. Falling back to InMemoryStore.",
                RuntimeWarning,
                stacklevel=2,
            )
            return InMemoryStore()

    raise ValueError(f"unknown VECTOR_BACKEND: {backend!r} (expected: es|qdrant|inmemory)")
