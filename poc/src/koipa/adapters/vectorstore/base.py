"""Vector Store 추상.

v2.1 (2026-05-27): 하이브리드는 별도 Protocol로 분리.

- `VectorStore`: dense kNN 기본 계약. 모든 구현체가 만족.
- `HybridVectorStore`: 어휘 + dense + RRF 진짜 하이브리드. EsStore(BM25+nori) 및
  PgVectorStore(ts_rank+pg_bigm, 의사결정_대장 §03 경로 ⓑ)가 해당.
- 비-하이브리드 구현체(InMemory)도 `search_hybrid`를 제공하지만
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
    # #17: 재인덱싱 고아 청크 제거용 삭제 계약. ids(정확 id) 또는 filter(예: doc_id)
    # 중 적어도 하나로 대상 지정. 삭제 건수를 반환. 대상이 없으면 0(멱등).
    def delete(
        self,
        collection: str,
        *,
        ids: Sequence[str] | None = None,
        filter: dict | None = None,
    ) -> int: ...
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
    """어휘검색 + dense + RRF를 실제로 결합하는 백엔드 마커.

    EsStore(BM25+nori)·PgVectorStore(ts_rank+pg_bigm)가 이 Protocol을 만족. P2 측정 등
    진짜 하이브리드 결과가 필요한 호출부는 `isinstance(vs, HybridVectorStore)`로 분기한다.
    """
