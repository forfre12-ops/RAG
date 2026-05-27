"""RAG 인덱서 — 가이드 문서 텍스트 → 청크 → 임베딩 → ES 적재.

설계 (doc/13 §4.1 + OpenAPI /guide/documents):
- 인덱스 명명: `secrets-guides-{tenant_id}-{model}-{version}` 패턴.
  alias `secrets-guides-{tenant_id}`로 라우팅 → Blue/Green 무중단 스왑.
- 청크: m2_preprocess.split (size=512, overlap=64).
- 임베딩: KURE-v1 (기본) / HashEmbedding (드라이런·오프라인 폴백).
- 적재: EsStore.upsert (bulk index).
- ES 미가용·임베딩 미가용 시 indexed=False, embedding_vector_count=0으로 안전 반환.

운영 흐름:
1) GuideService.upload(file)에서 본 인덱서 호출
2) RagIndexer.index_guide(guide_id, version, tenant_id, text) → IndexResult
3) GuideService가 IndexResult를 받아 indexed=True, vector_count 갱신
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
    """가이드 문서를 ES `secrets-guides-{tenant_id}-{model}-{version}` 인덱스에 적재.

    부품 주입형 — 테스트에서는 InMemoryStore + HashEmbedding으로 격리.
    운영에서는 build_store()/build_embedder()의 기본 결정.
    """

    def __init__(
        self,
        store: Optional[VectorStore] = None,
        *,
        embedder=None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ):
        self.store = store if store is not None else build_store()
        self.embedder = embedder if embedder is not None else build_embedder()
        self.chunk_size = chunk_size or settings.chunk_size
        self.chunk_overlap = chunk_overlap or settings.chunk_overlap

    # ------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------

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
    ) -> IndexResult:
        """가이드 문서 1건을 ES에 적재. 성공 시 alias를 새 인덱스로 스왑.

        실패 (ES 미가용, 임베딩 오류, 청크 0개)는 IndexResult.indexed=False로
        조용히 반환하고 warnings에 사유 누적.
        """
        warns: list[str] = []
        alias = self._alias_name(tenant_id)
        model_safe = _safe_token(self.embedder.name if hasattr(self.embedder, "name") else "emb")
        version_safe = _safe_token(version)
        new_index = f"{alias}-{model_safe}-{version_safe}"

        # 1) 청크 분할
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

        # 2) 임베딩
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

        # 3) 인덱스 생성
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

        # 4) 페이로드 + upsert
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        ids = [f"{guide_id}:v{version_safe}:c{i}" for i in range(len(chunks))]
        payloads = [
            {
                "id": ids[i],
                "doc_id": guide_id,
                "chunk_idx": i,
                "tenant_id": tenant_id,
                "grade": None,  # 가이드는 등급 라벨이 없음 — null
                "doc_type": doc_type,
                "version": version,
                "effective_date": effective_date,
                "created_at": now,
                "text": chunk_texts[i],
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

        # 5) alias 스왑 — EsStore만 지원. 다른 백엔드는 silent skip.
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

    # ------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------

    @staticmethod
    def _alias_name(tenant_id: str) -> str:
        safe = _safe_token(tenant_id)
        return f"secrets-guides-{safe}"

    def _current_alias_target(self, alias: str) -> str | None:
        """현재 alias가 가리키는 인덱스명. EsStore만 지원.

        ES 클라이언트에 직접 접근. 실패하면 None.
        """
        client = getattr(self.store, "_client", None)
        if client is None:
            return None
        try:
            resp = client.indices.get_alias(name=alias)
            if not resp:
                return None
            # 첫 인덱스명 반환 (운영은 일반적으로 1개만 활성)
            return next(iter(resp.keys()), None)
        except Exception:  # noqa: BLE001
            return None


# ------------------------------------------------------------
# helpers
# ------------------------------------------------------------


_TOKEN_RE = re.compile(r"[^a-z0-9._\-]")


def _safe_token(value: str) -> str:
    """ES 인덱스명에 안전한 토큰으로 변환 (lowercase, alnum + . _ -)."""
    if not value:
        return "default"
    lower = value.lower().replace("/", "-").replace(" ", "-")
    safe = _TOKEN_RE.sub("-", lower)
    safe = safe.strip("-._")
    if not safe:
        # 알 수 없는 문자 전부 → 해시 8자
        safe = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
    return safe[:50]  # ES 인덱스명 길이 제한 회피
