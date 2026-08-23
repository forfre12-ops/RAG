"""ORM round-trip tests against a live Postgres.

DESIGN: tests are skipped automatically when Postgres is not reachable.
This keeps CI green when only the dryrun stack is available, while still
exercising the full ORM mapping when a developer (or CI matrix job) has
the docker-compose `postgres` service running.

Trigger: `docker compose up -d postgres` then `pytest tests/test_db_models.py`.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError

from koipa.db import Base, SessionLocal, engine, session_scope
from koipa.db.models import (
    AuditLog,
    Classification,
    ClassificationEvidence,
    ClassificationLevel,
    Correction,
    Document,
    EvaluationFactor,
    LlmUsage,
)


def _postgres_available() -> bool:
    """Quick reachability probe — true only if the engine can open a conn."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.fullstack


def _require_postgres() -> None:
    if not _postgres_available():
        pytest.skip(
            "Postgres not reachable on settings.database_url - start docker compose up -d postgres"
        )


# ============================================================
# Metadata consistency
# ============================================================

def test_orm_metadata_has_expected_tables():
    """Sanity: all ORM-mapped parent tables present in Base.metadata."""
    expected = {                          # ORM __tablename__ 은 tb_ 접두사 규약
        "tb_classification_levels",
        "tb_evaluation_factors",
        "tb_level_keywords",
        "tb_documents",
        "tb_chunks",
        "tb_document_labels",
        "tb_document_factor_scores",
        "tb_classifications",
        "tb_classification_evidence",
        "tb_model_versions",
        "tb_training_runs",
        "tb_training_epochs",
        "tb_training_datasets",
        "tb_corrections",
        "tb_prompt_versions",
        "tb_sample_documents",
        "tb_llm_usage",
        "tb_audit_log",
        "tb_guides",          # N5: GuideService DB 이전
    }
    actual = set(Base.metadata.tables.keys())
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"missing ORM tables: {missing}"
    assert not extra, f"unexpected ORM tables: {extra}"


def test_orm_tables_present_in_database():
    """All ORM-mapped tables must exist in the real Postgres schema."""
    _require_postgres()
    insp = inspect(engine)
    db_tables = set(insp.get_table_names())
    orm_tables = set(Base.metadata.tables.keys())
    missing = orm_tables - db_tables
    assert not missing, (
        f"ORM tables not found in DB: {missing}. "
        "Did you run `docker compose up -d postgres && alembic upgrade head`?"
    )


# ============================================================
# Seed data sanity
# tenant 제거: 격리는 KL 포털 전담(단일 고객사 엔진). tenants 시드/round-trip 삭제.
# ============================================================

def test_classification_levels_seeded():
    """alembic baseline seeds 4 levels with OpenAPI-aligned codes."""
    _require_postgres()
    with session_scope() as s:
        codes = [
            lvl.level_code
            for lvl in s.query(ClassificationLevel)
            .order_by(ClassificationLevel.level_order)
            .all()
        ]
        assert codes == ["TS", "S1", "S2", "S3"]


def test_evaluation_factors_seeded():
    """alembic baseline seeds the 4 grade factors with weights summing to 1.0.

    공유 DB라 타 테스트가 추가 factor를 남길 수 있으므로 baseline 4요소의
    존재·가중합만 검증(exact-match 금지).
    """
    _require_postgres()
    with session_scope() as s:
        factors = {f.factor_code: float(f.weight) for f in s.query(EvaluationFactor).all()}
        baseline = {"ECONOMIC_VALUE", "NON_PUBLICITY", "MANAGEMENT_LEVEL", "LEAK_IMPACT"}
        assert baseline.issubset(factors.keys()), f"baseline 4요소 누락: {baseline - factors.keys()}"
        total = sum(factors[c] for c in baseline)
        assert abs(total - 1.0) < 0.01, f"baseline factor weights must sum to ~1.0 (got {total})"


# ============================================================
# Round-trip: insert + read + delete (kept in transaction, rolled back)
# ============================================================

@pytest.fixture
def db_session():
    """Provide a session and roll back at the end — keeps the DB clean."""
    _require_postgres()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_document_round_trip(db_session):
    doc = Document(
        filename="sample.pdf",
        source_format="pdf",
        file_size_bytes=12345,
        file_hash="abc" * 21 + "d",  # 64 chars
        text_preview="hello world",
        char_count=11,
        processing_status="pending",
    )
    db_session.add(doc)
    db_session.flush()
    assert isinstance(doc.doc_id, uuid.UUID)
    fetched = db_session.get(Document, doc.doc_id)
    assert fetched.filename == "sample.pdf"
    # metadata default = {}
    assert fetched.metadata_ == {}


def test_classification_round_trip_with_evidence(db_session):
    doc = Document(
        filename="x.pdf",
        source_format="pdf",
        processing_status="done",
    )
    db_session.add(doc)
    db_session.flush()

    # Use the seeded S1 level (id is auto-assigned but stable since 4 rows are seeded)
    s1 = (
        db_session.query(ClassificationLevel)
        .filter(ClassificationLevel.level_code == "S1")
        .one()
    )

    cls = Classification(
        doc_id=doc.doc_id,
        model_version="v0-test",
        predicted_level_id=s1.level_id,
        confidence=0.8765,
        alternatives=[{"level_code": "TS", "confidence": 0.0935}],
        status="staging",
    )
    db_session.add(cls)
    db_session.flush()

    ev = ClassificationEvidence(
        classification_id=cls.classification_id,
        chunk_id=uuid.uuid4(),
        evidence_type="keyword_match",
        excerpt="hello",
        contribution=0.500,
    )
    db_session.add(ev)
    db_session.flush()

    # Round-trip read
    fetched = db_session.get(Classification, cls.classification_id)
    assert fetched.alternatives == [{"level_code": "TS", "confidence": 0.0935}]
    assert float(fetched.confidence) == pytest.approx(0.8765, abs=1e-4)
    assert fetched.status == "staging"

    evs = (
        db_session.query(ClassificationEvidence)
        .filter(ClassificationEvidence.classification_id == cls.classification_id)
        .all()
    )
    assert len(evs) == 1
    assert evs[0].evidence_type == "keyword_match"


def test_correction_links_to_classification(db_session):
    doc = Document(filename="y.pdf", source_format="pdf")
    db_session.add(doc)
    db_session.flush()

    s1 = db_session.query(ClassificationLevel).filter_by(level_code="S1").one()
    ts = db_session.query(ClassificationLevel).filter_by(level_code="TS").one()

    cls = Classification(
        doc_id=doc.doc_id,
        model_version="v0",
        predicted_level_id=s1.level_id,
        confidence=0.9,
        alternatives=[],
    )
    db_session.add(cls)
    db_session.flush()

    corr = Correction(
        classification_id=cls.classification_id,
        original_level_id=s1.level_id,
        corrected_level_id=ts.level_id,
        direction="underclass",
        reason="security keyword missed",
        corrected_by="admin@test",
    )
    db_session.add(corr)
    db_session.flush()

    fetched = (
        db_session.query(Correction)
        .filter_by(classification_id=cls.classification_id)
        .one()
    )
    assert fetched.direction == "underclass"
    assert fetched.consumed_in_run is None  # not yet consumed by AL


def test_audit_log_partition_insert(db_session):
    """audit_log is a RANGE-partitioned parent — insert must route to a child."""
    a = AuditLog(
        action="classify",
        actor_id="kl-user-1",
        actor_role="admin",
        target_type="document",
        target_id=str(uuid.uuid4()),
        success=True,
    )
    db_session.add(a)
    db_session.flush()
    assert a.audit_id is not None
    assert a.occurred_at is not None


def test_llm_usage_partition_insert(db_session):
    u = LlmUsage(
        provider="anthropic",
        model="claude-sonnet-4-6",
        purpose="sample_generation",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.001234,
        billing_phase="development",
    )
    db_session.add(u)
    db_session.flush()
    assert u.usage_id is not None
    assert u.called_at is not None


def test_session_scope_commits_and_rolls_back():
    """session_scope: commits on success, rolls back on exception.

    tenant 제거: 격리는 KL 포털 전담. 과거 Tenant 모델 대신 Document로 검증.
    """
    _require_postgres()
    marker = f"scope-{uuid.uuid4().hex[:8]}"

    # Success path: commit
    with session_scope() as s:
        doc = Document(filename=marker, source_format="txt")
        s.add(doc)
        s.flush()
        committed_id = doc.doc_id
    with session_scope() as s:
        assert s.get(Document, committed_id) is not None

    # Rollback path: exception → no insert
    with pytest.raises(RuntimeError):
        with session_scope() as s:
            doc = Document(filename=f"rb-{marker}", source_format="txt")
            s.add(doc)
            s.flush()
            raise RuntimeError("intentional")
    # rollback이라 별도 세션에서 조회되지 않아야 함 — filename 기준으로 부재 확인
    with session_scope() as s:
        leaked = s.query(Document).filter_by(filename=f"rb-{marker}").first()
        assert leaked is None

    # Cleanup: delete the committed test document so re-runs stay clean
    with session_scope() as s:
        d = s.get(Document, committed_id)
        if d is not None:
            s.delete(d)
