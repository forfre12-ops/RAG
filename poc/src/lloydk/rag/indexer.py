"""RAG 인덱서 — 문서 텍스트 → 청크 → 임베딩 → 벡터스토어 적재.

도메인 독립 코어. 인덱스 명명 패턴만 바꾸면 어느 프로젝트에서나 재사용 가능.

사용 예:
    from lloydk.rag.indexer import RagIndexer

    indexer = RagIndexer()  # settings 기반 자동 결정
    result = indexer.index_guide(
        guide_id="doc-001", version="v1", text="..."
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
# #15: v1 split(순수 문자 단위)은 한국어 조항/문장 중간을 절단하고 heading_path
#       메타를 유실한다. 운영 권장 split_v2(헤딩·문장 경계 보존)로 전환한다.
from lloydk.modules.m2_preprocess import split_v2 as chunk_split

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

    인덱스 명명 패턴: `{collection_prefix}-{model}-{version}`
    alias로 라우팅 → Blue/Green 무중단 스왑 지원.
    (tenant 제거: 격리는 KL 포털 전담 — 단일 고객사 엔진이라 인덱스명 per-customer 분리 불요)

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
        self.chunk_size = chunk_size or settings.rag_index_chunk_size
        self.chunk_overlap = chunk_overlap or settings.rag_index_chunk_overlap
        self.collection_prefix = collection_prefix

    def index_guide(
        self,
        *,
        guide_id: str,
        version: str,
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
        alias = self._alias_name()
        model_safe = _safe_token(self.embedder.name if hasattr(self.embedder, "name") else "emb")
        version_safe = _safe_token(version)
        new_index = f"{alias}-{model_safe}-{version_safe}"

        # #15: split_v2 — 헤딩/문장 경계 보존. 청크 크기 정책(size/overlap)은 기존
        #      설정값(rag_index_chunk_size/overlap)을 그대로 전달해 보존한다.
        chunks = chunk_split(
            text,
            size=self.chunk_size,
            overlap=self.chunk_overlap,
            respect_heading=True,
            respect_sentence=True,
        )
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
                "doc_type": doc_type,
                "version": version,
                "effective_date": effective_date,
                "created_at": now,
                "text": chunk_texts[i],
                # #15: split_v2가 부여한 섹션 경로 — 검색·표시·reranking에 활용.
                "heading_path": list(chunks[i].heading_path),
                # #15: 청크 추가 메타(overlap 길이) — 위치/경계 정보 가능 범위 보존.
                "overlap_prev": chunks[i].overlap_prev,
                "overlap_next": chunks[i].overlap_next,
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

    def _alias_name(self) -> str:
        # tenant 제거: 격리는 KL 포털 전담 — alias는 collection_prefix 단일값.
        return f"{self.collection_prefix}"

    def _current_alias_target(self, alias: str) -> str | None:
        # 비-ES 스토어(PgVectorStore 등)는 current_alias_target 훅을 제공 — ES `_client`
        # 하드코딩을 벗어나 백엔드 무관하게 blue/green 이전 인덱스를 조회한다.
        hook = getattr(self.store, "current_alias_target", None)
        if callable(hook):
            try:
                return hook(alias)
            except Exception:  # noqa: BLE001
                return None
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


_TOKEN_MAX_LEN = 50
# #37: truncation 시 충돌 방지용 해시 suffix 규격. "-" + sha1 앞 8자 = 9자.
_HASH_SUFFIX_LEN = 8


def _safe_token(value: str) -> str:
    """벡터스토어 인덱스명에 안전한 토큰으로 변환 (lowercase, alnum + . _ -)."""
    if not value:
        return "default"
    lower = value.lower().replace("/", "-").replace(" ", "-")
    safe = _TOKEN_RE.sub("-", lower)
    safe = safe.strip("-._")
    if not safe:
        safe = hashlib.sha1(value.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
    # #37 (인덱스명 충돌): 50자 truncation이 실제로 일어나면 앞 50자가 같은
    #      서로 다른 model/version이 같은 인덱스/alias로 붕괴할 수 있다. truncation이
    #      발생한 경우에 한해 원본(value)의 짧은 해시 suffix를 부착해 항상 유일성을
    #      보장한다. (truncation이 없으면 suffix 미부착 — 기존 인덱스명 그대로 보존, 비파괴.)
    if len(safe) > _TOKEN_MAX_LEN:
        suffix = hashlib.sha1(value.encode("utf-8"), usedforsecurity=False).hexdigest()[:_HASH_SUFFIX_LEN]
        head_len = _TOKEN_MAX_LEN - _HASH_SUFFIX_LEN - 1  # "-" 1자 확보
        head = safe[:head_len].strip("-._")
        return f"{head}-{suffix}"
    return safe
