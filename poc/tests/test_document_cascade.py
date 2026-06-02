"""Document ↔ Chunk cascade 삭제 테스트.

검증 목표:
- ``DocumentService.delete_document`` 가 트랜잭션 한 번에 chunks → documents
  → audit_log 순으로 cascade 한다.
- SQLAlchemy ``before_delete`` 이벤트 리스너는 ORM ``Session.delete(doc)``
  경로에서도 chunks 를 자동 정리한다 (안전망).
- tenant_id 격리 — 다른 테넌트의 청크는 영향 없음.
- 빈 chunk 상태에서 Document 삭제도 정상 처리.

skip 정책:
- PG 미가용 시 전체 skip — test_repositories.py 및 test_db_models.py 와
  동일한 reachability probe 사용.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from lloydk.db import SessionLocal, engine
from lloydk.db.models import AuditLog, Chunk, Document, Tenant
from lloydk.repositories import ChunkRepo, DocumentRepo
from lloydk.services.document_service import DocumentService


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


@pytest.fixture
def tenant_id(db) -> str:
    tid = f"t-{uuid.uuid4().hex[:8]}"
    db.add(Tenant(tenant_id=tid, name=f"Tenant {tid}"))
    db.flush()
    return tid


@pytest.fixture
def second_tenant_id(db) -> str:
    """tenant 격리 검증용 두 번째 테넌트."""
    tid = f"u-{uuid.uuid4().hex[:8]}"
    db.add(Tenant(tenant_id=tid, name=f"Tenant {tid}"))
    db.flush()
    return tid


def _make_document(db, tenant_id: str, filename: str = "doc.pdf") -> Document:
    doc = Document(
        tenant_id=tenant_id,
        filename=filename,
        source_format="pdf",
        processing_status="done",
    )
    db.add(doc)
    db.flush()
    return doc


def _make_chunks(db, doc_id: uuid.UUID, tenant_id: str, n: int = 3) -> list[Chunk]:
    """파티션은 created_at=NOW() 기준 자동 라우팅 (2026-05 파티션 hit)."""
    chunks: list[Chunk] = []
    for i in range(n):
        c = Chunk(
            doc_id=doc_id,
            tenant_id=tenant_id,
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
    def test_deletes_all_chunks_for_doc(self, db, tenant_id):
        doc = _make_document(db, tenant_id)
        _make_chunks(db, doc.doc_id, tenant_id, n=3)

        repo = ChunkRepo(db)
        assert repo.count_by_doc_id(doc.doc_id, tenant_id) == 3

        deleted = repo.delete_by_doc_id(doc.doc_id, tenant_id)
        db.flush()
        assert deleted == 3
        assert repo.count_by_doc_id(doc.doc_id, tenant_id) == 0

    def test_requires_tenant_id(self, db, tenant_id):
        doc = _make_document(db, tenant_id)
        _make_chunks(db, doc.doc_id, tenant_id, n=1)

        repo = ChunkRepo(db)
        with pytest.raises(ValueError, match="tenant_id is required"):
            repo.delete_by_doc_id(doc.doc_id, "")

    def test_tenant_isolation(self, db, tenant_id, second_tenant_id):
        """tenant_A 삭제가 tenant_B chunk 에 영향 없음."""
        doc_a = _make_document(db, tenant_id, filename="a.pdf")
        doc_b = _make_document(db, second_tenant_id, filename="b.pdf")
        _make_chunks(db, doc_a.doc_id, tenant_id, n=2)
        _make_chunks(db, doc_b.doc_id, second_tenant_id, n=2)

        repo = ChunkRepo(db)
        # tenant_A 의 doc_b doc_id 로 삭제 시도 — tenant_id 불일치라 0건
        deleted = repo.delete_by_doc_id(doc_b.doc_id, tenant_id)
        db.flush()
        assert deleted == 0
        assert repo.count_by_doc_id(doc_b.doc_id, second_tenant_id) == 2


# ============================================================
# 2b. DocumentService.delete_document — 명시 cascade
# ============================================================

class TestDocumentServiceDeleteDocument:
    def test_cascade_chunks_and_document_in_one_transaction(self, db, tenant_id):
        doc = _make_document(db, tenant_id)
        _make_chunks(db, doc.doc_id, tenant_id, n=3)

        chunk_repo = ChunkRepo(db)
        doc_repo = DocumentRepo(db)
        assert chunk_repo.count_by_doc_id(doc.doc_id, tenant_id) == 3
        assert doc_repo.get(doc.doc_id) is not None

        service = DocumentService()
        result = service.delete_document(
            doc_id=doc.doc_id,
            tenant_id=tenant_id,
            actor_id="reviewer@test",
            actor_role="admin",
            db=db,
        )

        assert result.document_deleted == 1
        assert result.chunks_deleted == 3
        assert result.audit_recorded is True

        db.flush()
        assert chunk_repo.count_by_doc_id(doc.doc_id, tenant_id) == 0
        assert doc_repo.get(doc.doc_id) is None

    def test_empty_chunks_state_is_handled(self, db, tenant_id):
        """청크 없는 Document 삭제 — 0개 chunk 삭제, 1개 doc 삭제."""
        doc = _make_document(db, tenant_id, filename="empty.pdf")

        service = DocumentService()
        result = service.delete_document(
            doc_id=doc.doc_id,
            tenant_id=tenant_id,
            actor_id="reviewer@test",
            db=db,
        )

        assert result.document_deleted == 1
        assert result.chunks_deleted == 0
        assert result.audit_recorded is True

    def test_audit_log_recorded(self, db, tenant_id):
        doc = _make_document(db, tenant_id, filename="audited.pdf")
        _make_chunks(db, doc.doc_id, tenant_id, n=1)
        doc_id_str = str(doc.doc_id)

        DocumentService().delete_document(
            doc_id=doc.doc_id,
            tenant_id=tenant_id,
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

    def test_tenant_isolation_in_service(self, db, tenant_id, second_tenant_id):
        """tenant_A service.delete 가 tenant_B doc/chunk 를 건드리지 않음."""
        doc_a = _make_document(db, tenant_id, filename="a.pdf")
        doc_b = _make_document(db, second_tenant_id, filename="b.pdf")
        _make_chunks(db, doc_a.doc_id, tenant_id, n=2)
        _make_chunks(db, doc_b.doc_id, second_tenant_id, n=2)

        # tenant_A 가 doc_b 를 삭제 시도 — tenant_id 불일치
        result = DocumentService().delete_document(
            doc_id=doc_b.doc_id,
            tenant_id=tenant_id,  # wrong tenant
            actor_id="attacker",
            db=db,
        )
        db.flush()

        assert result.document_deleted == 0
        assert result.chunks_deleted == 0

        # doc_b 와 그 chunks 는 보존
        chunk_repo = ChunkRepo(db)
        doc_repo = DocumentRepo(db)
        assert doc_repo.get(doc_b.doc_id) is not None
        assert chunk_repo.count_by_doc_id(doc_b.doc_id, second_tenant_id) == 2


# ============================================================
# 2c. SQLAlchemy event listener — ORM 경로 안전망
# ============================================================

class TestCascadeListener:
    def test_orm_session_delete_triggers_chunk_cascade(self, db, tenant_id):
        """Service 우회하고 ``Session.delete(doc)`` 만 호출해도 chunks 정리."""
        doc = _make_document(db, tenant_id, filename="listener.pdf")
        _make_chunks(db, doc.doc_id, tenant_id, n=2)

        chunk_repo = ChunkRepo(db)
        assert chunk_repo.count_by_doc_id(doc.doc_id, tenant_id) == 2

        # ORM cascade 경로 — before_delete 이벤트 발화
        db.delete(doc)
        db.flush()

        assert chunk_repo.count_by_doc_id(doc.doc_id, tenant_id) == 0
        assert db.get(Document, doc.doc_id) is None
