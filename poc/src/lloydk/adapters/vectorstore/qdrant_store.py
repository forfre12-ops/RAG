"""Qdrant 1.10+ store."""

from __future__ import annotations

from typing import Sequence

from lloydk.adapters.vectorstore.base import SearchHit


class QdrantStore:
    name = "qdrant"

    def __init__(self, url: str | None = None) -> None:
        from qdrant_client import QdrantClient

        from lloydk.config import settings

        self._client = QdrantClient(url=url or settings.qdrant_url)

    def ensure_collection(self, name: str, dim: int) -> None:
        from qdrant_client.http import models as qm

        existing = {c.name for c in self._client.get_collections().collections}
        if name in existing:
            return
        self._client.create_collection(
            collection_name=name,
            vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
        )

    def upsert(
        self,
        collection: str,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        payloads: Sequence[dict] | None = None,
    ) -> int:
        from qdrant_client.http import models as qm

        payloads = payloads or [{} for _ in ids]
        points = [
            qm.PointStruct(id=_id, vector=list(vec), payload=dict(pl))
            for _id, vec, pl in zip(ids, vectors, payloads, strict=True)
        ]
        self._client.upsert(collection_name=collection, points=points)
        return len(points)

    def search(
        self,
        collection: str,
        query: Sequence[float],
        top_k: int = 5,
        filter: dict | None = None,
    ) -> list[SearchHit]:
        from qdrant_client.http import models as qm

        qfilter = None
        if filter:
            qfilter = qm.Filter(
                must=[qm.FieldCondition(key=k, match=qm.MatchValue(value=v)) for k, v in filter.items()]
            )
        results = self._client.search(
            collection_name=collection,
            query_vector=list(query),
            limit=top_k,
            query_filter=qfilter,
        )
        return [SearchHit(id=str(r.id), score=float(r.score), payload=dict(r.payload or {})) for r in results]

    def search_hybrid(
        self,
        collection: str,
        query_text: str,  # noqa: ARG002 — Qdrant 폴리필은 dense-only
        query_vec: Sequence[float],
        top_k: int = 5,
        filter: dict | None = None,
        **_kwargs: object,
    ) -> list[SearchHit]:
        """Qdrant 롤백 경로 폴리필: query_text 무시, dense kNN만 사용."""
        return self.search(collection, query_vec, top_k=top_k, filter=filter)

    def count(self, collection: str) -> int:
        return self._client.count(collection_name=collection, exact=True).count
