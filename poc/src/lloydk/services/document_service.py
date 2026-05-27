"""Document 라이프사이클 서비스 — 업로드 외 삭제 cascade 책임.

배경:
- ``documents`` ↔ ``chunks`` 는 PG 16에서 FK로 묶을 수 없음 (chunks가
  RANGE PARTITION 부모 — 자식 파티션은 FK 수신 가능하나 파티션 부모는
  여러 운영상 제약이 있어 init.sql v2 도 FK 미설정).
- 따라서 Document 삭제 시 Chunk 행을 함께 정리하지 않으면 고아 누적 →
  감사 추적성 훼손 (doc/04 §9.5) + 스토리지 회수 불가.
- 본 서비스는 한 트랜잭션에서 ``chunks DELETE → documents DELETE →
  audit_log INSERT`` 순서를 보장한다. SQLAlchemy ``before_delete`` 리스너는
  실수 안전망이며, 본 서비스가 1차 진실 경로.

설계 노트:
- Service는 ``session_scope`` 컨텍스트를 직접 열거나, 외부 주입 세션을
  쓸 수 있도록 두 API 를 제공 (테스트 격리·기존 흐름 호환).
- tenant_id는 모든 경로에서 강제 검증. cross-tenant 삭제는
  ValueError 가 아닌 단순 0행 반환으로 처리 (정보 누출 방지).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from lloydk.db import session_scope
from lloydk.repositories.audit_repo import AuditRepo
from lloydk.repositories.chunk_repo import ChunkRepo
from lloydk.repositories.document_repo import DocumentRepo

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeleteDocumentResult:
    document_deleted: int  # 0 또는 1
    chunks_deleted: int
    audit_recorded: bool


class DocumentService:
    """Document 도메인 라이프사이클 서비스."""

    @staticmethod
    def _coerce_uuid(value: uuid.UUID | str) -> uuid.UUID:
        if isinstance(value, uuid.UUID):
            return value
        return uuid.UUID(str(value))

    def delete_document(
        self,
        *,
        doc_id: uuid.UUID | str,
        tenant_id: str,
        actor_id: Optional[str] = None,
        actor_role: Optional[str] = None,
        request_id: uuid.UUID | str | None = None,
        db: Optional[Session] = None,
    ) -> DeleteDocumentResult:
        """Document + 종속 Chunk 트랜잭션 cascade 삭제.

        흐름:
          1) ChunkRepo.delete_by_doc_id  (tenant 격리 강제)
          2) DocumentRepo.delete         (없으면 0행 — idempotent)
          3) AuditRepo.record            (action=document.delete)

        외부 세션이 주입되면 트랜잭션 경계는 호출자가 책임지고, 미주입 시
        ``session_scope`` 로 자체 commit/rollback 한다.
        """
        if not tenant_id:
            raise ValueError("tenant_id is required for document delete")
        doc_uuid = self._coerce_uuid(doc_id)

        if db is not None:
            return self._delete_with_session(
                db,
                doc_id=doc_uuid,
                tenant_id=tenant_id,
                actor_id=actor_id,
                actor_role=actor_role,
                request_id=request_id,
            )

        with session_scope() as scoped:
            return self._delete_with_session(
                scoped,
                doc_id=doc_uuid,
                tenant_id=tenant_id,
                actor_id=actor_id,
                actor_role=actor_role,
                request_id=request_id,
            )

    @staticmethod
    def _delete_with_session(
        db: Session,
        *,
        doc_id: uuid.UUID,
        tenant_id: str,
        actor_id: Optional[str],
        actor_role: Optional[str],
        request_id: uuid.UUID | str | None,
    ) -> DeleteDocumentResult:
        chunk_repo = ChunkRepo(db)
        doc_repo = DocumentRepo(db)
        audit_repo = AuditRepo(db)

        chunks_deleted = chunk_repo.delete_by_doc_id(doc_id, tenant_id)
        document_deleted = doc_repo.delete(doc_id, tenant_id)

        audit_repo.record(
            action="document.delete",
            actor_id=actor_id,
            actor_role=actor_role,
            tenant_id=tenant_id,
            target_type="document",
            target_id=str(doc_id),
            request_id=request_id,
            payload={
                "doc_id": str(doc_id),
                "tenant_id": tenant_id,
                "chunks_deleted": chunks_deleted,
                "document_deleted": document_deleted,
            },
            success=document_deleted == 1,
            error_code=None if document_deleted == 1 else "not_found_or_cross_tenant",
        )

        logger.info(
            "document.delete: doc_id=%s tenant=%s chunks_deleted=%d doc_deleted=%d actor=%s",
            doc_id, tenant_id, chunks_deleted, document_deleted, actor_id,
        )

        return DeleteDocumentResult(
            document_deleted=document_deleted,
            chunks_deleted=chunks_deleted,
            audit_recorded=True,
        )


__all__ = ["DocumentService", "DeleteDocumentResult"]
