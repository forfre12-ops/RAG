"""Vector Store — Elasticsearch(기본) / InMemory(dryrun).

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

from lloydk.adapters.vectorstore.base import HybridVectorStore, SearchHit, VectorStore
from lloydk.adapters.vectorstore.inmemory_store import InMemoryStore

__all__ = ["VectorStore", "HybridVectorStore", "SearchHit", "InMemoryStore", "build_store"]

_STORE_CACHE: dict[str, VectorStore] = {}


def build_store(
    *,
    backend: str | None = None,
    force_memory: bool = False,
) -> VectorStore:
    """프로세스 내 싱글톤 — ES 연결 수립 비용을 최초 1회로 한정."""
    if force_memory:
        return InMemoryStore()

    backend = (backend or os.getenv("VECTOR_BACKEND", "es")).lower()

    if backend == "inmemory":
        return InMemoryStore()

    if backend in _STORE_CACHE:
        return _STORE_CACHE[backend]

    if backend == "es":
        try:
            from lloydk.adapters.vectorstore.es_store import EsStore  # noqa: PLC0415

            store = EsStore()
            _STORE_CACHE[backend] = store
            return store
        except Exception as exc:  # noqa: BLE001
            warnings.warn(
                f"[vectorstore] Elasticsearch unavailable: {exc}. Falling back to InMemoryStore.",
                RuntimeWarning,
                stacklevel=2,
            )
            return InMemoryStore()

    raise ValueError(f"unknown VECTOR_BACKEND: {backend!r} (expected: es|inmemory)")
