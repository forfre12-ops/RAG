"""Vector Store — Postgres/pgvector(기본) / Elasticsearch(레거시) / InMemory(dryrun).

의사결정_대장 §03(ES→Postgres 단일화)으로 기본 백엔드가 pg 로 전환됨(2026-06-24).
ES 경로는 하위호환용 레거시로만 잔존(core/prod/airgap compose 에서 ES 서비스 제거됨).

backend 결정 우선순위:
  1. 명시적 인자 `backend=...`
  2. 검증·프로필 적용이 끝난 `settings.vector_backend`

`force_memory=True`는 테스트용 강제 inmemory.
"""

from __future__ import annotations

import warnings

from koipa.adapters.vectorstore.base import HybridVectorStore, SearchHit, VectorStore
from koipa.adapters.vectorstore.inmemory_store import InMemoryStore

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

    # Settings is the single source of truth for runtime configuration.  It
    # includes profile defaults and validation, unlike a late raw env lookup.
    if backend is None:
        from koipa.config import settings  # noqa: PLC0415

        backend = settings.vector_backend
    backend = backend.lower()

    if backend == "inmemory":
        return InMemoryStore()

    if backend in _STORE_CACHE:
        return _STORE_CACHE[backend]

    if backend == "es":
        try:
            from koipa.adapters.vectorstore.es_store import EsStore  # noqa: PLC0415

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

    # PG-native (의사결정_대장 §03 경로 ⓑ) — 현재 기본 백엔드.
    # ⚠️ 라이브 PG(pgvector 이미지) × 실코퍼스 × 자연어(비발췌) 쿼리 재검증 게이트는 미실행
    #    (실 PG 부재). 단위·정적 검증은 통과(test_pg_store). 실측 R@5 확정 후 caveat 제거 예정.
    # 연결은 지연(SQLAlchemy 풀) — 생성 시 미접속, 쿼리 시점에 오류 표면화(es 와 동일 정책).
    if backend in ("pg", "pgvector", "postgres"):
        from koipa.adapters.vectorstore.pg_store import PgVectorStore  # noqa: PLC0415

        store = PgVectorStore()
        _STORE_CACHE[backend] = store
        return store

    raise ValueError(f"unknown VECTOR_BACKEND: {backend!r} (expected: es|pg|inmemory)")
