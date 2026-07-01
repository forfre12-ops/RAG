"""Chunk 도메인 — 파티션 부모 테이블 ``chunks`` 대상 CRUD.

배경:
- ``chunks``는 RANGE PARTITION BY (created_at). PG 16에서도 파티션 부모는
  자식 → 부모 FK를 받을 수 없어, ``documents.doc_id``와의 무결성을
  DB 레벨로 강제할 수 없음.
- 따라서 Document 삭제 시 cascade는 애플리케이션 레이어 책임이며,
  본 리포지토리에서 ``delete_by_doc_id``를 제공한다.

설계 노트:
- 모든 메서드는 외부 Session을 주입받고, commit/rollback은 호출자(Service)
  의 ``session_scope`` 에서 일괄 처리.
- tenant 제거: 격리는 KL 포털 전담(단일 고객사 엔진, 전역 조회).
"""

from __future__ import annotations

import uuid
from typing import Iterable, Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from lloydk.db.models import Chunk

# 표적 2 (2026-05-29): chunk 영속화는 ClassifyService가 호출.
# tiktoken 미설치 환경에서도 동작하도록 token_count는 char_count/4 휴리스틱으로 폴백.


def _estimate_token_count(text: str) -> int:
    """tiktoken 미설치 환경 폴백 — 한국어/영문 혼용 ≈ char/4 (보수적)."""
    return max(1, len(text) // 4)


class ChunkRepo:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------

    def count_by_doc_id(self, doc_id: uuid.UUID | str) -> int:
        """특정 문서의 chunk 수. cascade 검증·테스트용."""
        from sqlalchemy import func as _func

        stmt = (
            select(_func.count())
            .select_from(Chunk)
            .where(Chunk.doc_id == doc_id)
        )
        return int(self.db.execute(stmt).scalar() or 0)

    def get_by_doc_id(
        self,
        doc_id: uuid.UUID | str,
        *,
        limit: int | None = None,
    ) -> list[Chunk]:
        stmt = (
            select(Chunk)
            .where(Chunk.doc_id == doc_id)
            .order_by(Chunk.chunk_index)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.execute(stmt).scalars())

    # ------------------------------------------------------------
    # Insert (표적 2)
    # ------------------------------------------------------------

    def upsert_chunks(
        self,
        *,
        doc_id: uuid.UUID,
        chunks: Iterable,
        replace_existing: bool = True,
    ) -> list[uuid.UUID]:
        """PreprocessPipeline의 chunks(Chunk dataclass) → DB chunks 테이블 일괄 insert.

        Args:
            doc_id: 소속 문서 UUID (FK는 파티션 제약으로 DB에 없음, app 레이어 책임)
            chunks: PreprocessPipeline.chunk() 또는 PreprocessResult.chunks (Chunk dataclass)
                    필수 속성: index, text, char_count, overlap_prev, overlap_next
            replace_existing: True면 같은 doc_id의 기존 chunks 먼저 삭제 (재처리 시 중복 방지)

        Returns:
            insert된 chunk_id 리스트 (PG가 server_default로 채움 → flush 후 확보).
        """
        if replace_existing:
            self.delete_by_doc_id(doc_id)

        rows: list[Chunk] = []
        for c in chunks:
            text = getattr(c, "text", None)
            if not text:
                continue
            row = Chunk(
                doc_id=doc_id,
                chunk_index=int(getattr(c, "index", len(rows))),
                content=text,
                token_count=_estimate_token_count(text),
                char_count=int(getattr(c, "char_count", len(text))),
                overlap_prev=int(getattr(c, "overlap_prev", 0) or 0),
                overlap_next=int(getattr(c, "overlap_next", 0) or 0),
            )
            self.db.add(row)
            rows.append(row)
        if rows:
            self.db.flush()
        return [r.chunk_id for r in rows]

    def chunk_ids_by_index(
        self,
        doc_id: uuid.UUID | str,
        indices: Sequence[int],
    ) -> dict[int, uuid.UUID]:
        """chunk_index → chunk_id 매핑. Evidence 영속화 시 진짜 chunk_id 조회용."""
        if not indices:
            return {}
        rows = (
            self.db.execute(
                select(Chunk.chunk_index, Chunk.chunk_id)
                .where(
                    Chunk.doc_id == doc_id,
                    Chunk.chunk_index.in_(list(indices)),
                )
            )
        ).all()
        return {int(idx): cid for idx, cid in rows}

    # ------------------------------------------------------------
    # Cascade delete
    # ------------------------------------------------------------

    def delete_by_doc_id(
        self,
        doc_id: uuid.UUID | str,
    ) -> int:
        """문서 단위 청크 삭제. 반환값은 삭제된 행 수.

        tenant 제거: 격리는 KL 포털 전담(단일 고객사 엔진).
        파티션 테이블이라 PG가 자동으로 모든 자식 파티션을 순회한다.
        """
        stmt = delete(Chunk).where(Chunk.doc_id == doc_id)
        result = self.db.execute(stmt)
        # rowcount는 driver에 따라 -1일 수 있으나 psycopg2/asyncpg는 정확.
        return int(result.rowcount or 0)
