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
- ``delete_by_doc_id``는 tenant_id를 강제로 받아 cross-tenant 삭제 사고를
  원천 차단 (영업비밀 시스템 멀티 테넌트 격리 요구).
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from lloydk.db.models import Chunk


class ChunkRepo:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------

    def count_by_doc_id(self, doc_id: uuid.UUID | str, tenant_id: str) -> int:
        """특정 문서의 chunk 수. cascade 검증·테스트용."""
        from sqlalchemy import func as _func

        stmt = (
            select(_func.count())
            .select_from(Chunk)
            .where(Chunk.doc_id == doc_id, Chunk.tenant_id == tenant_id)
        )
        return int(self.db.execute(stmt).scalar() or 0)

    # ------------------------------------------------------------
    # Cascade delete
    # ------------------------------------------------------------

    def delete_by_doc_id(
        self,
        doc_id: uuid.UUID | str,
        tenant_id: str,
    ) -> int:
        """문서 단위 청크 삭제. 반환값은 삭제된 행 수.

        tenant_id는 필수 인자 — 멀티 테넌트 격리 강제.
        파티션 테이블이라 PG가 자동으로 모든 자식 파티션을 순회한다.
        """
        if not tenant_id:
            raise ValueError("tenant_id is required for chunk cascade delete")

        stmt = delete(Chunk).where(
            Chunk.doc_id == doc_id,
            Chunk.tenant_id == tenant_id,
        )
        result = self.db.execute(stmt)
        # rowcount는 driver에 따라 -1일 수 있으나 psycopg2/asyncpg는 정확.
        return int(result.rowcount or 0)
