"""Repository round-trip tests against live Postgres.

PG 미가용 시 자동 skip — test_db_models.py와 동일 패턴.
모든 테스트는 rollback fixture로 격리되어 DB 상태를 오염시키지 않음.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from lloydk.db import SessionLocal, engine
from lloydk.db.models import (
    Classification,
    ClassificationLevel,
    Correction,
    Document,
    Tenant,
)
from lloydk.repositories import (
    AuditRepo,
    ClassifyRepo,
    LlmUsageRepo,
    SynthRepo,
    TrainingRepo,
)


def _pg_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _pg_ok(),
    reason="Postgres not reachable — start docker compose up -d postgres",
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def db():
    """rollback fixture — 각 테스트 종료 시 모든 변경 폐기."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def tenant_id(db) -> str:
    """매 테스트마다 고유 tenant_id 생성 (rollback에서 정리됨)."""
    tid = f"t-{uuid.uuid4().hex[:8]}"
    db.add(Tenant(tenant_id=tid, name=f"Tenant {tid}"))
    db.flush()
    return tid


@pytest.fixture
def document(db, tenant_id) -> Document:
    doc = Document(
        tenant_id=tenant_id,
        filename="doc.pdf",
        source_format="pdf",
        processing_status="done",
    )
    db.add(doc)
    db.flush()
    return doc


@pytest.fixture
def levels(db) -> dict[str, int]:
    """seed된 4 등급의 (code → level_id) 매핑."""
    rows = db.query(ClassificationLevel).all()
    return {lv.level_code: lv.level_id for lv in rows}


# ============================================================
# ClassifyRepo
# ============================================================

class TestClassifyRepo:
    def test_lookup_helpers(self, db, tenant_id, document):
        repo = ClassifyRepo(db)
        assert repo.tenant_exists(tenant_id) is True
        assert repo.tenant_exists("does-not-exist") is False
        assert repo.document_exists(document.doc_id) is True
        assert repo.document_exists(uuid.uuid4()) is False
        assert repo.level_id_by_code("TS") is not None
        assert repo.level_id_by_code("NOPE") is None

    def test_create_classification(self, db, tenant_id, document, levels):
        repo = ClassifyRepo(db)
        cls = repo.create_classification(
            doc_id=document.doc_id,
            tenant_id=tenant_id,
            model_version="v-test",
            predicted_level_id=levels["S1"],
            confidence=0.92,
            alternatives=[{"level_code": "TS", "confidence": 0.05}],
            chunk_count=3,
            rag_used=True,
            rag_top_k=5,
        )
        assert cls.classification_id is not None
        assert cls.status == "staging"
        assert float(cls.confidence) == pytest.approx(0.92, abs=1e-4)

    def test_evidence_round_trip_from_spans(self, db, tenant_id, document, levels):
        from lloydk.schemas.classify import EvidenceSpan

        repo = ClassifyRepo(db)
        cls = repo.create_classification(
            doc_id=document.doc_id,
            tenant_id=tenant_id,
            model_version="v",
            predicted_level_id=levels["TS"],
            confidence=0.85,
            alternatives=[],
        )
        spans = [
            EvidenceSpan(start=0, end=4, text="특급기밀", weight=0.8, tag="keyword_match"),
            EvidenceSpan(start=10, end=14, text="설계도", weight=0.6),
        ]
        added = repo.add_evidence_from_spans(
            cls.classification_id, spans=spans, default_chunk_id=uuid.uuid4()
        )
        assert added == 2
        db.flush()

        from lloydk.db.models import ClassificationEvidence

        evs = (
            db.query(ClassificationEvidence)
            .filter_by(classification_id=cls.classification_id)
            .all()
        )
        assert len(evs) == 2

    def test_correction_direction_underclass(self, db, tenant_id, document, levels):
        """원본 S2(3) → 보정 TS(1): order 감소 = underclass (보안 미탐)."""
        repo = ClassifyRepo(db)
        cls = repo.create_classification(
            doc_id=document.doc_id,
            tenant_id=tenant_id,
            model_version="v",
            predicted_level_id=levels["S2"],
            confidence=0.7,
            alternatives=[],
        )
        corr = repo.add_correction(
            classification_id=cls.classification_id,
            original_level_id=levels["S2"],
            corrected_level_id=levels["TS"],
            corrected_by="reviewer@test",
            reason="missed top-secret keyword",
        )
        assert corr.direction == "underclass"

    def test_correction_direction_overclass_and_confirm(self, db, tenant_id, document, levels):
        repo = ClassifyRepo(db)
        cls = repo.create_classification(
            doc_id=document.doc_id,
            tenant_id=tenant_id,
            model_version="v",
            predicted_level_id=levels["TS"],
            confidence=0.6,
            alternatives=[],
        )
        # TS(1) → S2(3): order 증가 = overclass
        over = repo.add_correction(
            classification_id=cls.classification_id,
            original_level_id=levels["TS"],
            corrected_level_id=levels["S2"],
            corrected_by="r",
        )
        assert over.direction == "overclass"

        # 같은 등급 → confirm
        conf = repo.add_correction(
            classification_id=cls.classification_id,
            original_level_id=levels["S1"],
            corrected_level_id=levels["S1"],
            corrected_by="r",
        )
        assert conf.direction == "confirm"

    def test_mark_corrections_consumed(self, db, tenant_id, document, levels):
        repo = ClassifyRepo(db)
        cls = repo.create_classification(
            doc_id=document.doc_id,
            tenant_id=tenant_id,
            model_version="v",
            predicted_level_id=levels["S2"],
            confidence=0.5,
            alternatives=[],
        )
        corr = repo.add_correction(
            classification_id=cls.classification_id,
            original_level_id=levels["S2"],
            corrected_level_id=levels["S1"],
            corrected_by="r",
        )
        assert corr.consumed_in_run is None

        # 실제 training_run을 만들고 consumed 마크
        train_repo = TrainingRepo(db)
        run = train_repo.create_run(total_samples=100)

        marked = repo.mark_corrections_consumed(run.run_id, [corr.correction_id])
        assert marked == 1
        db.flush()

        refreshed = db.get(Correction, corr.correction_id)
        assert refreshed.consumed_in_run == run.run_id
        assert refreshed.consumed_at is not None


# ============================================================
# TrainingRepo
# ============================================================

class TestTrainingRepo:
    def test_run_lifecycle(self, db):
        repo = TrainingRepo(db)
        run = repo.create_run(
            total_samples=500,
            train_count=350, val_count=75, test_count=75,
            hyperparameters={"lr": 2e-5, "epochs": 5},
            trigger_type="manual",
        )
        assert run.status == "queued"

        repo.mark_started(run.run_id)
        db.flush()
        run_refreshed = repo.get_run(run.run_id)
        assert run_refreshed.status == "running"
        assert run_refreshed.started_at is not None

        repo.log_epoch(run.run_id, 1, train_loss=0.5, val_loss=0.6, val_metrics={"f1": 0.7})
        repo.log_epoch(run.run_id, 2, train_loss=0.3, val_loss=0.4, val_metrics={"f1": 0.85})
        db.flush()
        epochs = repo.epochs_for_run(run.run_id)
        assert [e.epoch for e in epochs] == [1, 2]

        repo.mark_completed(run.run_id, final_metrics={"f1_macro": 0.85, "fnr": 0.04}, duration_sec=1234)
        db.flush()
        run_done = repo.get_run(run.run_id)
        assert run_done.status == "completed"
        assert run_done.duration_sec == 1234
        assert run_done.final_metrics["f1_macro"] == 0.85

    def test_model_version_register_and_activate(self, db):
        repo = TrainingRepo(db)
        run = repo.create_run(total_samples=100)
        mv = repo.register_model_version(
            version_label=f"v-test-{uuid.uuid4().hex[:6]}",
            base_model="kakaobank/kf-deberta-base",
            metrics={"f1_macro": 0.82},
            training_run_id=run.run_id,
        )
        assert mv.is_active is False

        repo.activate_model_version(mv.version_id)
        db.flush()
        active = repo.get_active()
        assert active is not None
        assert active.version_id == mv.version_id

    def test_register_dataset_rows(self, db, tenant_id, document, levels):
        repo = TrainingRepo(db)
        run = repo.create_run(total_samples=1)
        # 같은 문서를 한 split에만 등록 (UNIQUE(run_id, doc_id))
        count = repo.register_dataset_rows(
            run.run_id, [(document.doc_id, "train", levels["S2"])]
        )
        assert count == 1


# ============================================================
# SynthRepo
# ============================================================

class TestSynthRepo:
    def test_sample_lifecycle(self, db, tenant_id, levels):
        repo = SynthRepo(db)
        sd = repo.create_sample(
            tenant_id=tenant_id,
            target_level_id=levels["S1"],
            llm_provider="anthropic",
            llm_model="claude-sonnet-4-6",
            generated_content="합성 본문 ...",
            doc_type="기술도면",
            quality_score=0.88,
        )
        assert sd.review_status == "pending_review"

        pending = repo.list_pending_review(tenant_id=tenant_id)
        assert any(s.sample_id == sd.sample_id for s in pending)

        reviewed = repo.review(sd.sample_id, approved=True, reviewed_by="qa@test")
        assert reviewed is not None
        assert reviewed.review_status == "approved"
        assert reviewed.reviewed_at is not None

    def test_review_rejection(self, db, tenant_id, levels):
        repo = SynthRepo(db)
        sd = repo.create_sample(
            tenant_id=tenant_id,
            target_level_id=levels["S3"],
            llm_provider="noop",
            llm_model="noop",
            generated_content="garbage",
        )
        rej = repo.review(
            sd.sample_id, approved=False, reviewed_by="qa", rejection_reason="low quality"
        )
        assert rej.review_status == "rejected"
        assert rej.rejection_reason == "low quality"

    def test_upsert_prompt(self, db):
        repo = SynthRepo(db)
        v = f"p-{uuid.uuid4().hex[:6]}"
        p1 = repo.upsert_prompt(v, chain_stage="outline", template="t1")
        assert p1.template == "t1"
        p2 = repo.upsert_prompt(v, chain_stage="outline", template="t2", notes="updated")
        assert p2.template == "t2"
        assert p2.notes == "updated"


# ============================================================
# AuditRepo
# ============================================================

class TestAuditRepo:
    def test_record_with_dict_payload(self, db):
        repo = AuditRepo(db)
        a = repo.record(
            action="classify",
            actor_id="user-a",
            actor_role="admin",
            payload={"doc_id": "abc", "content_length": 100},
        )
        db.flush()
        assert a.payload_hash is not None
        assert len(a.payload_hash) == 64  # SHA-256 hex

    def test_record_with_explicit_hash(self, db):
        repo = AuditRepo(db)
        a = repo.record(
            action="classify",
            actor_id="user-b",
            payload_hash="deadbeef" * 8,
        )
        db.flush()
        assert a.payload_hash == "deadbeef" * 8

    def test_recent_for_actor_filters_correctly(self, db):
        repo = AuditRepo(db)
        marker = f"actor-{uuid.uuid4().hex[:8]}"
        repo.record(action="classify", actor_id=marker)
        repo.record(action="confirm", actor_id=marker)
        repo.record(action="classify", actor_id="other")
        db.flush()
        rows = repo.recent_for_actor(marker)
        assert len(rows) == 2
        assert all(r.actor_id == marker for r in rows)


# ============================================================
# LlmUsageRepo
# ============================================================

class TestLlmUsageRepo:
    def test_record_and_aggregate(self, db, tenant_id):
        repo = LlmUsageRepo(db)
        repo.record(
            provider="anthropic",
            model="claude-sonnet-4-6",
            purpose="sample_generation",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.01,
            tenant_id=tenant_id,
            billing_phase="development",
        )
        repo.record(
            provider="anthropic",
            model="claude-sonnet-4-6",
            purpose="sample_generation",
            input_tokens=2000,
            output_tokens=800,
            cost_usd=0.02,
            tenant_id=tenant_id,
            billing_phase="development",
        )
        db.flush()
        # rollback fixture라 같은 트랜잭션 내에서만 보임
        in_tokens, out_tokens = repo.total_tokens(billing_phase="development")
        assert in_tokens >= 3000
        assert out_tokens >= 1300
        cost = repo.total_cost_usd(billing_phase="development", tenant_id=tenant_id)
        assert cost >= 0.029  # 0.01 + 0.02 (부동소수 오차 여유)
