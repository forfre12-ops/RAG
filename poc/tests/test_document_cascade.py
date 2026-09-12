"""Document ↔ Chunk cascade 삭제 테스트.

검증 목표:
- ``DocumentService.delete_document`` 가 트랜잭션 한 번에 chunks → documents
  → audit_log 순으로 cascade 한다.
- SQLAlchemy ``before_delete`` 이벤트 리스너는 ORM ``Session.delete(doc)``
  경로에서도 chunks 를 자동 정리한다 (안전망).
- 빈 chunk 상태에서 Document 삭제도 정상 처리.

tenant 제거: 격리는 KL 포털 전담(단일 고객사 엔진). per-tenant 격리 검증 케이스는
기능 제거로 삭제됨.

skip 정책:
- PG 미가용 시 전체 skip — test_repositories.py 및 test_db_models.py 와
  동일한 reachability probe 사용.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from koipa.db import SessionLocal, engine
from koipa.db.models import AuditLog, Chunk, Document
from koipa.repositories import ChunkRepo, DocumentRepo
from koipa.services.document_service import DocumentService


def _pg_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.fullstack


def _require_pg() -> None:
    if not _pg_ok():
        pytest.skip("Postgres not reachable - start docker compose up -d postgres")


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def db():
    """rollback fixture — 트랜잭션 격리."""
    _require_pg()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _make_document(db, filename: str = "doc.pdf") -> Document:
    doc = Document(
        filename=filename,
        source_format="pdf",
        processing_status="done",
    )
    db.add(doc)
    db.flush()
    return doc


def _make_chunks(db, doc_id: uuid.UUID, n: int = 3) -> list[Chunk]:
    """파티션은 created_at=NOW() 기준 자동 라우팅 (2026-05 파티션 hit)."""
    chunks: list[Chunk] = []
    for i in range(n):
        c = Chunk(
            doc_id=doc_id,
            chunk_index=i,
            content=f"chunk content #{i}",
            token_count=10,
            char_count=20,
        )
        db.add(c)
        chunks.append(c)
    db.flush()
    return chunks


# ============================================================
# 2a. ChunkRepo.delete_by_doc_id
# ============================================================

class TestChunkRepoDeleteByDocId:
    def test_deletes_all_chunks_for_doc(self, db):
        doc = _make_document(db)
        _make_chunks(db, doc.doc_id, n=3)

        repo = ChunkRepo(db)
        assert repo.count_by_doc_id(doc.doc_id) == 3

        deleted = repo.delete_by_doc_id(doc.doc_id)
        db.flush()
        assert deleted == 3
        assert repo.count_by_doc_id(doc.doc_id) == 0


# ============================================================
# 2b. DocumentService.delete_document — 명시 cascade
# ============================================================

class TestDocumentServiceDeleteDocument:
    def test_cascade_chunks_and_document_in_one_transaction(self, db):
        doc = _make_document(db)
        _make_chunks(db, doc.doc_id, n=3)

        chunk_repo = ChunkRepo(db)
        doc_repo = DocumentRepo(db)
        assert chunk_repo.count_by_doc_id(doc.doc_id) == 3
        assert doc_repo.get(doc.doc_id) is not None

        service = DocumentService()
        result = service.delete_document(
            doc_id=doc.doc_id,
            actor_id="reviewer@test",
            actor_role="admin",
            db=db,
        )

        assert result.document_deleted == 1
        assert result.chunks_deleted == 3
        assert result.audit_recorded is True

        db.flush()
        assert chunk_repo.count_by_doc_id(doc.doc_id) == 0
        assert doc_repo.get(doc.doc_id) is None

    def test_empty_chunks_state_is_handled(self, db):
        """청크 없는 Document 삭제 — 0개 chunk 삭제, 1개 doc 삭제."""
        doc = _make_document(db, filename="empty.pdf")

        service = DocumentService()
        result = service.delete_document(
            doc_id=doc.doc_id,
            actor_id="reviewer@test",
            db=db,
        )

        assert result.document_deleted == 1
        assert result.chunks_deleted == 0
        assert result.audit_recorded is True

    def test_audit_log_recorded(self, db):
        doc = _make_document(db, filename="audited.pdf")
        _make_chunks(db, doc.doc_id, n=1)
        doc_id_str = str(doc.doc_id)

        DocumentService().delete_document(
            doc_id=doc.doc_id,
            actor_id="reviewer@test",
            actor_role="admin",
            db=db,
        )
        db.flush()

        # 같은 트랜잭션 내라 rollback fixture 가 정리하기 전에 보임
        audits = (
            db.query(AuditLog)
            .filter(
                AuditLog.target_type == "document",
                AuditLog.target_id == doc_id_str,
                AuditLog.action == "document.delete",
            )
            .all()
        )
        assert len(audits) >= 1
        assert audits[0].actor_id == "reviewer@test"
        assert audits[0].success is True


# ============================================================
# 2c. SQLAlchemy event listener — ORM 경로 안전망
# ============================================================

class TestCascadeListener:
    def test_orm_session_delete_triggers_chunk_cascade(self, db):
        """Service 우회하고 ``Session.delete(doc)`` 만 호출해도 chunks 정리."""
        doc = _make_document(db, filename="listener.pdf")
        _make_chunks(db, doc.doc_id, n=2)

        chunk_repo = ChunkRepo(db)
        assert chunk_repo.count_by_doc_id(doc.doc_id) == 2

        # ORM cascade 경로 — before_delete 이벤트 발화
        db.delete(doc)
        db.flush()

        assert chunk_repo.count_by_doc_id(doc.doc_id) == 0
        assert db.get(Document, doc.doc_id) is None
