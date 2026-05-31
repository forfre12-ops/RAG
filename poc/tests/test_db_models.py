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

from lloydk.db import Base, SessionLocal, engine, session_scope
from lloydk.db.models import (
    AuditLog,
    Classification,
    ClassificationEvidence,
    ClassificationLevel,
    Correction,
    Document,
    EvaluationFactor,
    LlmUsage,
    Tenant,
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


_PG_OK = _postgres_available()

pytestmark = pytest.mark.skipif(
    not _PG_OK,
    reason="Postgres not reachable on settings.database_url — start docker compose up -d postgres",
)


# ============================================================
# Metadata consistency
# ============================================================

def test_orm_metadata_has_expected_tables():
    """Sanity: all ORM-mapped parent tables present in Base.metadata."""
    expected = {
        "tenants",
        "classification_levels",
        "evaluation_factors",
        "level_keywords",
        "documents",
        "chunks",
        "document_labels",
        "document_factor_scores",
        "classifications",
        "classification_evidence",
        "model_versions",
        "training_runs",
        "training_epochs",
        "training_datasets",
        "corrections",
        "prompt_versions",
        "sample_documents",
        "llm_usage",
        "audit_log",
        "guides",          # N5: GuideService DB 이전
    }
    actual = set(Base.metadata.tables.keys())
    missing = expected - actual
    extra = actual - expected
    assert not missing, f"missing ORM tables: {missing}"
    assert not extra, f"unexpected ORM tables: {extra}"


def test_orm_tables_present_in_database():
    """All ORM-mapped tables must exist in the real Postgres schema."""
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
# ============================================================

def test_default_tenant_seeded():
    with session_scope() as s:
        t = s.get(Tenant, "default")
        assert t is not None
        assert t.is_active is True
        assert t.rag_enabled is True


def test_classification_levels_seeded():
    """alembic baseline seeds 4 levels with OpenAPI-aligned codes."""
    with session_scope() as s:
        codes = [
            lvl.level_code
            for lvl in s.query(ClassificationLevel)
            .order_by(ClassificationLevel.level_order)
            .all()
        ]
        assert codes == ["TS", "S1", "S2", "S3"]


def test_evaluation_factors_seeded():
    """alembic baseline seeds 4 factors with weights summing to 1.0."""
    with session_scope() as s:
        factors = s.query(EvaluationFactor).all()
        codes = {f.factor_code for f in factors}
        assert codes == {
            "ECONOMIC_VALUE",
            "NON_PUBLICITY",
            "MANAGEMENT_LEVEL",
            "LEAK_IMPACT",
        }
        total = sum(float(f.weight) for f in factors)
        assert abs(total - 1.0) < 0.01, f"factor weights must sum to ~1.0 (got {total})"


# ============================================================
# Round-trip: insert + read + delete (kept in transaction, rolled back)
# ============================================================

@pytest.fixture
def db_session():
    """Provide a session and roll back at the end — keeps the DB clean."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _new_tenant(s, tenant_id: str | None = None) -> Tenant:
    tid = tenant_id or f"test-{uuid.uuid4().hex[:8]}"
    t = Tenant(tenant_id=tid, name=f"Tenant {tid}", is_active=True)
    s.add(t)
    s.flush()
    return t


def test_tenant_round_trip(db_session):
    t = _new_tenant(db_session, "rtrip-1")
    fetched = db_session.get(Tenant, "rtrip-1")
    assert fetched is not None
    assert fetched.name == "Tenant rtrip-1"
    assert fetched.tenant_id == t.tenant_id


def test_document_round_trip(db_session):
    t = _new_tenant(db_session)
    doc = Document(
        tenant_id=t.tenant_id,
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
    assert fetched.tenant_id == t.tenant_id
    # metadata default = {}
    assert fetched.metadata_ == {}


def test_classification_round_trip_with_evidence(db_session):
    t = _new_tenant(db_session)
    doc = Document(
        tenant_id=t.tenant_id,
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
        tenant_id=t.tenant_id,
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
    t = _new_tenant(db_session)
    doc = Document(tenant_id=t.tenant_id, filename="y.pdf", source_format="pdf")
    db_session.add(doc)
    db_session.flush()

    s1 = db_session.query(ClassificationLevel).filter_by(level_code="S1").one()
    ts = db_session.query(ClassificationLevel).filter_by(level_code="TS").one()

    cls = Classification(
        doc_id=doc.doc_id,
        tenant_id=t.tenant_id,
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
    """session_scope: commits on success, rolls back on exception."""
    marker = f"scope-{uuid.uuid4().hex[:8]}"

    # Success path: commit
    with session_scope() as s:
        s.add(Tenant(tenant_id=marker, name="scope test"))
    with session_scope() as s:
        assert s.get(Tenant, marker) is not None

    # Rollback path: exception → no insert
    rollback_marker = f"rb-{uuid.uuid4().hex[:8]}"
    with pytest.raises(RuntimeError):
        with session_scope() as s:
            s.add(Tenant(tenant_id=rollback_marker, name="should not persist"))
            s.flush()
            raise RuntimeError("intentional")
    with session_scope() as s:
        assert s.get(Tenant, rollback_marker) is None

    # Cleanup: delete the committed test tenant so re-runs stay clean
    with session_scope() as s:
        t = s.get(Tenant, marker)
        if t is not None:
            s.delete(t)
