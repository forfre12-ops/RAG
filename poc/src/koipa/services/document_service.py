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
- tenant 제거: 격리는 KL 포털 전담(단일 고객사 엔진). 삭제는 doc_id 단독으로
  스코프하며, 없는 문서는 단순 0행 반환으로 멱등 처리한다.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from koipa.db import session_scope
from koipa.repositories.audit_repo import AuditRepo
from koipa.repositories.chunk_repo import ChunkRepo
from koipa.repositories.document_repo import DocumentRepo

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeleteDocumentResult:
    document_deleted: int  # 0 또는 1
    chunks_deleted: int
    audit_recorded: bool
    # #38 soft-delete 경로일 때 True. 기본(물리 cascade) 경로는 False라 기존 비파괴.
    soft_deleted: bool = False


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
        actor_id: Optional[str] = None,
        actor_role: Optional[str] = None,
        request_id: uuid.UUID | str | None = None,
        db: Optional[Session] = None,
        soft: bool = False,
    ) -> DeleteDocumentResult:
        """Document + 종속 Chunk 트랜잭션 cascade 삭제.

        흐름(기본, soft=False — 물리삭제, 기존 동작 보존):
          1) ChunkRepo.delete_by_doc_id  (doc_id 단독)
          2) DocumentRepo.delete         (없으면 0행 — idempotent)
          3) AuditRepo.record            (action=document.delete)

        외부 세션이 주입되면 트랜잭션 경계는 호출자가 책임지고, 미주입 시
        ``session_scope`` 로 자체 commit/rollback 한다.

        #38: soft=True면 물리삭제/cascade 대신 Document.deleted_at만 세팅하는
        논리 삭제 경로로 분기(opt-in). 청크/감사 행은 보존되며 조회에서만 숨는다.
        보존기간 만료 후 물리 회수(purge)는 별도 운영 잡 — 본 서비스 범위 밖.
        """
        doc_uuid = self._coerce_uuid(doc_id)

        runner = self._soft_delete_with_session if soft else self._delete_with_session

        if db is not None:
            return runner(
                db,
                doc_id=doc_uuid,
                actor_id=actor_id,
                actor_role=actor_role,
                request_id=request_id,
            )

        with session_scope() as scoped:
            return runner(
                scoped,
                doc_id=doc_uuid,
                actor_id=actor_id,
                actor_role=actor_role,
                request_id=request_id,
            )

    def soft_delete_document(
        self,
        *,
        doc_id: uuid.UUID | str,
        actor_id: Optional[str] = None,
        actor_role: Optional[str] = None,
        request_id: uuid.UUID | str | None = None,
        db: Optional[Session] = None,
    ) -> DeleteDocumentResult:
        """#38 논리 삭제 편의 진입점 — ``delete_document(..., soft=True)`` 위임.

        기존 ``delete_document`` 시그니처/기본 동작을 건드리지 않고 soft-delete를
        명시적으로 호출하고 싶은 경로용. 보존정책 준수(데이터 보존 + 조회 차단).
        """
        return self.delete_document(
            doc_id=doc_id,
            actor_id=actor_id,
            actor_role=actor_role,
            request_id=request_id,
            db=db,
            soft=True,
        )

    @staticmethod
    def _delete_with_session(
        db: Session,
        *,
        doc_id: uuid.UUID,
        actor_id: Optional[str],
        actor_role: Optional[str],
        request_id: uuid.UUID | str | None,
    ) -> DeleteDocumentResult:
        chunk_repo = ChunkRepo(db)
        doc_repo = DocumentRepo(db)
        audit_repo = AuditRepo(db)

        chunks_deleted = chunk_repo.delete_by_doc_id(doc_id)
        document_deleted = doc_repo.delete(doc_id)

        audit_repo.record(
            action="document.delete",
            actor_id=actor_id,
            actor_role=actor_role,
            target_type="document",
            target_id=str(doc_id),
            request_id=request_id,
            payload={
                "doc_id": str(doc_id),
                "chunks_deleted": chunks_deleted,
                "document_deleted": document_deleted,
            },
            success=document_deleted == 1,
            error_code=None if document_deleted == 1 else "not_found",
        )

        logger.info(
            "document.delete: doc_id=%s chunks_deleted=%d doc_deleted=%d actor=%s",
            doc_id, chunks_deleted, document_deleted, actor_id,
        )

        return DeleteDocumentResult(
            document_deleted=document_deleted,
            chunks_deleted=chunks_deleted,
            audit_recorded=True,
        )

    @staticmethod
    def _soft_delete_with_session(
        db: Session,
        *,
        doc_id: uuid.UUID,
        actor_id: Optional[str],
        actor_role: Optional[str],
        request_id: uuid.UUID | str | None,
    ) -> DeleteDocumentResult:
        """#38 논리 삭제 — Document.deleted_at만 세팅. 청크/감사 행은 보존.

        물리 cascade와 달리 ChunkRepo는 건드리지 않는다(데이터 보존이 목적).
        감사 로그는 action=document.soft_delete 로 별도 기록.
        """
        doc_repo = DocumentRepo(db)
        audit_repo = AuditRepo(db)

        document_deleted = doc_repo.soft_delete(doc_id)

        audit_repo.record(
            action="document.soft_delete",
            actor_id=actor_id,
            actor_role=actor_role,
            target_type="document",
            target_id=str(doc_id),
            request_id=request_id,
            payload={
                "doc_id": str(doc_id),
                "document_deleted": document_deleted,
                "soft": True,
            },
            success=document_deleted == 1,
            error_code=None if document_deleted == 1 else "not_found_or_already_deleted",
        )

        logger.info(
            "document.soft_delete: doc_id=%s doc_deleted=%d actor=%s",
            doc_id, document_deleted, actor_id,
        )

        return DeleteDocumentResult(
            document_deleted=document_deleted,
            chunks_deleted=0,  # 논리 삭제는 청크 보존
            audit_recorded=True,
            soft_deleted=True,
        )


__all__ = ["DocumentService", "DeleteDocumentResult"]
