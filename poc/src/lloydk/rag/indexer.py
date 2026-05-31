"""RAG 인덱서 — 문서 텍스트 → 청크 → 임베딩 → 벡터스토어 적재.

도메인 독립 코어. 인덱스 명명 패턴만 바꾸면 어느 프로젝트에서나 재사용 가능.

사용 예:
    from lloydk.rag.indexer import RagIndexer

    indexer = RagIndexer()  # settings 기반 자동 결정
    result = indexer.index_guide(
        guide_id="doc-001", version="v1", tenant_id="acme", text="..."
    )
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Optional

from lloydk.adapters.embedding import build_embedder
from lloydk.adapters.vectorstore import build_store
from lloydk.adapters.vectorstore.base import VectorStore
from lloydk.config import settings
from lloydk.modules.m2_preprocess import split as chunk_split

logger = logging.getLogger(__name__)


@dataclass
class IndexResult:
    indexed: bool
    index_name: str
    alias: str
    chunk_count: int
    vector_count: int
    model: str
    warnings: list[str]


class RagIndexer:
    """문서 텍스트를 벡터스토어에 적재하는 범용 인덱서.

    인덱스 명명 패턴: `{collection_prefix}-{tenant_id}-{model}-{version}`
    alias로 라우팅 → Blue/Green 무중단 스왑 지원.

    부품 주입형 — 테스트에서는 InMemoryStore + HashEmbedding으로 격리.
    운영에서는 build_store()/build_embedder()의 기본 결정.

    Args:
        collection_prefix: 인덱스 이름 앞에 붙는 prefix.
                           기본값 "secrets-guides". 다른 프로젝트에서 원하는 값으로 교체 가능.
    """

    def __init__(
        self,
        store: Optional[VectorStore] = None,
        *,
        embedder=None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        collection_prefix: str = "secrets-guides",
    ):
        self.store = store if store is not None else build_store()
        self.embedder = embedder if embedder is not None else build_embedder()
        self.chunk_size = chunk_size or settings.chunk_size
        self.chunk_overlap = chunk_overlap or settings.chunk_overlap
        self.collection_prefix = collection_prefix

    def index_guide(
        self,
        *,
        guide_id: str,
        version: str,
        tenant_id: str,
        text: str,
        doc_type: str | None = None,
        effective_date: str | None = None,
        switch_alias: bool = True,
        extra_payload: dict | None = None,
    ) -> IndexResult:
        """문서 1건을 벡터스토어에 적재. 성공 시 alias를 새 인덱스로 스왑.

        Args:
            extra_payload: 청크 페이로드에 추가할 필드. 도메인별 메타데이터 주입 가능.
        실패 (벡터스토어 미가용, 임베딩 오류, 청크 0개)는 IndexResult.indexed=False로
        조용히 반환하고 warnings에 사유 누적.
        """
        warns: list[str] = []
        alias = self._alias_name(tenant_id)
        model_safe = _safe_token(self.embedder.name if hasattr(self.embedder, "name") else "emb")
        version_safe = _safe_token(version)
        new_index = f"{alias}-{model_safe}-{version_safe}"

        chunks = chunk_split(text, size=self.chunk_size, overlap=self.chunk_overlap)
        if not chunks:
            warns.append("no chunks produced from text")
            return IndexResult(
                indexed=False,
                index_name=new_index,
                alias=alias,
                chunk_count=0,
                vector_count=0,
                model=getattr(self.embedder, "name", "unknown"),
                warnings=warns,
            )

        chunk_texts = [c.text for c in chunks]

        try:
            emb_result = self.embedder.embed(chunk_texts)
        except Exception as exc:  # noqa: BLE001
            warns.append(f"embedding failed: {type(exc).__name__}: {exc}")
            return IndexResult(
                indexed=False,
                index_name=new_index,
                alias=alias,
                chunk_count=len(chunks),
                vector_count=0,
                model=getattr(self.embedder, "name", "unknown"),
                warnings=warns,
            )

        try:
            self.store.ensure_collection(new_index, emb_result.dim)
        except Exception as exc:  # noqa: BLE001
            warns.append(f"ensure_collection failed: {type(exc).__name__}: {exc}")
            return IndexResult(
                indexed=False,
                index_name=new_index,
                alias=alias,
                chunk_count=len(chunks),
                vector_count=0,
                model=emb_result.model,
                warnings=warns,
            )

        now = dt.datetime.now(dt.timezone.utc).isoformat()
        ids = [f"{guide_id}:v{version_safe}:c{i}" for i in range(len(chunks))]
        base_extra = extra_payload or {}
        payloads = [
            {
                "id": ids[i],
                "doc_id": guide_id,
                "chunk_idx": i,
                "tenant_id": tenant_id,
                "doc_type": doc_type,
                "version": version,
                "effective_date": effective_date,
                "created_at": now,
                "text": chunk_texts[i],
                **base_extra,
            }
            for i in range(len(chunks))
        ]

        try:
            upsert_n = self.store.upsert(new_index, ids, emb_result.vectors, payloads)
        except Exception as exc:  # noqa: BLE001
            warns.append(f"upsert failed: {type(exc).__name__}: {exc}")
            return IndexResult(
                indexed=False,
                index_name=new_index,
                alias=alias,
                chunk_count=len(chunks),
                vector_count=0,
                model=emb_result.model,
                warnings=warns,
            )

        if switch_alias and hasattr(self.store, "swap_alias"):
            try:
                old_index = self._current_alias_target(alias)
                self.store.swap_alias(alias, new_index=new_index, old_index=old_index)
            except Exception as exc:  # noqa: BLE001
                warns.append(f"alias swap skipped: {type(exc).__name__}: {exc}")

        return IndexResult(
            indexed=True,
            index_name=new_index,
            alias=alias,
            chunk_count=len(chunks),
            vector_count=int(upsert_n),
            model=emb_result.model,
            warnings=warns,
        )

    def _alias_name(self, tenant_id: str) -> str:
        safe = _safe_token(tenant_id)
        return f"{self.collection_prefix}-{safe}"

    def _current_alias_target(self, alias: str) -> str | None:
        client = getattr(self.store, "_client", None)
        if client is None:
            return None
        try:
            resp = client.indices.get_alias(name=alias)
            if not resp:
                return None
            return next(iter(resp.keys()), None)
        except Exception:  # noqa: BLE001
            return None


_TOKEN_RE = re.compile(r"[^a-z0-9._\-]")


def _safe_token(value: str) -> str:
    """벡터스토어 인덱스명에 안전한 토큰으로 변환 (lowercase, alnum + . _ -)."""
    if not value:
        return "default"
    lower = value.lower().replace("/", "-").replace(" ", "-")
    safe = _TOKEN_RE.sub("-", lower)
    safe = safe.strip("-._")
    if not safe:
        safe = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
    return safe[:50]
