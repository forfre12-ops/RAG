"""Vector Store 추상.

v2.1 (2026-05-27): 하이브리드는 별도 Protocol로 분리.

- `VectorStore`: dense kNN 기본 계약. 모든 구현체가 만족.
- `HybridVectorStore`: BM25 + dense + RRF 진짜 하이브리드. EsStore만 해당.
- 비-하이브리드 구현체(Qdrant·InMemory)도 `search_hybrid`를 제공하지만
  **vec-only 폴리필**이며 호출 시 `RuntimeWarning`을 발생시켜 조용한 실패를 차단.
- 진짜 하이브리드가 필요한 호출부는 `isinstance(vs, HybridVectorStore)`로 분기.

배경: doc/13 §5.1의 단일 Protocol 설계는 LSP 위반 — 같은 시그니처가
구현체에 따라 다른 의미(진짜 하이브리드 vs dense-only)로 동작.
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


@runtime_checkable
class HybridVectorStore(VectorStore, Protocol):
    """BM25 + dense + RRF를 실제로 결합하는 백엔드 마커.

    EsStore만 이 Protocol을 만족. P2 측정 등 진짜 하이브리드 결과가 필요한
    호출부는 `isinstance(vs, HybridVectorStore)`로 분기해야 한다.
    """
