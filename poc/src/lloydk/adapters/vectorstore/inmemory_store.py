"""In-memory cosine similarity store. PoC·테스트용."""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from typing import Sequence

from lloydk.adapters.vectorstore.base import SearchHit


@dataclass
class _Collection:
    dim: int
    ids: list[str] = field(default_factory=list)
    vectors: list[list[float]] = field(default_factory=list)
    payloads: list[dict] = field(default_factory=list)


class InMemoryStore:
    name = "inmemory"

    def __init__(self) -> None:
        self._cols: dict[str, _Collection] = {}

    def ensure_collection(self, name: str, dim: int) -> None:
        col = self._cols.get(name)
        if col is None:
            self._cols[name] = _Collection(dim=dim)
        elif col.dim != dim:
            raise ValueError(f"dim mismatch on {name}: {col.dim} vs {dim}")

    def upsert(
        self,
        collection: str,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        payloads: Sequence[dict] | None = None,
    ) -> int:
        col = self._cols[collection]
        payloads = payloads or [{} for _ in ids]
        for _id, vec, pl in zip(ids, vectors, payloads, strict=True):
            if _id in col.ids:
                i = col.ids.index(_id)
                col.vectors[i] = list(vec)
                col.payloads[i] = dict(pl)
            else:
                col.ids.append(_id)
                col.vectors.append(list(vec))
                col.payloads.append(dict(pl))
        return len(ids)

    def search(
        self,
        collection: str,
        query: Sequence[float],
        top_k: int = 5,
        filter: dict | None = None,
    ) -> list[SearchHit]:
        col = self._cols[collection]
        scored: list[SearchHit] = []
        q = list(query)
        q_norm = math.sqrt(sum(x * x for x in q)) or 1.0
        for _id, vec, pl in zip(col.ids, col.vectors, col.payloads, strict=True):
            if filter and not all(pl.get(k) == v for k, v in filter.items()):
                continue
            v_norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            dot = sum(a * b for a, b in zip(q, vec, strict=True))
            scored.append(SearchHit(id=_id, score=dot / (q_norm * v_norm), payload=pl))
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:top_k]

    def search_hybrid(
        self,
        collection: str,
        query_text: str,  # noqa: ARG002 — BM25 미구현, vec-only 폴리필
        query_vec: Sequence[float],
        top_k: int = 5,
        filter: dict | None = None,
        **_kwargs: object,
    ) -> list[SearchHit]:
        """InMemory 폴리필: query_text 무시, vec-only 검색.

        진짜 하이브리드(BM25+dense+RRF)는 EsStore만 제공.
        조용한 실패를 막기 위해 호출 시 RuntimeWarning을 발생시킨다.
        하이브리드가 진짜 필요한 호출부는 `isinstance(vs, HybridVectorStore)`로
        분기해야 한다.
        """
        warnings.warn(
            "InMemoryStore.search_hybrid: BM25 미지원 → dense-only로 폴백. "
            "진짜 하이브리드 결과가 필요하면 EsStore를 사용하거나 "
            "isinstance(vs, HybridVectorStore)로 사전 분기하세요.",
            RuntimeWarning,
            stacklevel=2,
        )
        return self.search(collection, query_vec, top_k=top_k, filter=filter)

    def count(self, collection: str) -> int:
        return len(self._cols[collection].ids) if collection in self._cols else 0
