"""Document 도메인 — ``documents`` 부모 테이블 CRUD.

본 리포지토리는 ChunkRepo와 짝을 이뤄 cascade 삭제를 코드 레벨로 보장한다.
파티션 자식이 부모 FK를 가질 수 없는 PG 16 제약을 회피하기 위함이다.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete
from sqlalchemy.orm import Session

from lloydk.db.models import Document


class DocumentRepo:
    def __init__(self, db: Session):
        self.db = db

    def get(self, doc_id: uuid.UUID | str) -> Document | None:
        return self.db.get(Document, doc_id)

    def delete(self, doc_id: uuid.UUID | str, tenant_id: str) -> int:
        """단일 Document 삭제 — tenant 검증 포함.

        반환값: 삭제된 행 수 (0 또는 1).
        주의: Chunk cascade는 호출자가 ``ChunkRepo.delete_by_doc_id``를
        먼저 호출하거나 SQLAlchemy event listener에 위임해야 한다.
        본 메서드는 Document 단독 삭제만 책임진다.
        """
        if not tenant_id:
            raise ValueError("tenant_id is required for document delete")

        stmt = delete(Document).where(
            Document.doc_id == doc_id,
            Document.tenant_id == tenant_id,
        )
        result = self.db.execute(stmt)
        return int(result.rowcount or 0)
