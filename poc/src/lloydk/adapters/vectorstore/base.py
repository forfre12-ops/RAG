"""Vector Store 추상.

v2 (2026-05-27): `search_hybrid` 선택 메서드 추가.
- EsStore: BM25 + dense kNN + RRF 결합
- Qdrant/InMemory: vec-only 폴리필 (BM25 기여 미측정, P2 dryrun 한계)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, runtime_checkable


@dataclass
class SearchHit:
    id: str
    score: float
    payload: dict = field(default_factory=dict)


@runtime_checkable
class VectorStore(Protocol):
    name: str

    def ensure_collection(self, name: str, dim: int) -> None: ...
    def upsert(
        self,
        collection: str,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        payloads: Sequence[dict] | None = None,
    ) -> int: ...
    def search(
        self,
        collection: str,
        query: Sequence[float],
        top_k: int = 5,
        filter: dict | None = None,
    ) -> list[SearchHit]: ...
    def search_hybrid(
        self,
        collection: str,
        query_text: str,
        query_vec: Sequence[float],
        top_k: int = 5,
        filter: dict | None = None,
        **kwargs: Any,
    ) -> list[SearchHit]: ...
    def count(self, collection: str) -> int: ...
