"""Repository round-trip tests against live Postgres.

PG 미가용 시 자동 skip — test_db_models.py와 동일 패턴.
모든 테스트는 rollback fixture로 격리되어 DB 상태를 오염시키지 않음.
"""

from __future__ import annotations

import uuid

import pytest
from _pg_probe import postgres_available

from koipa.db import SessionLocal
from koipa.db.models import (
    ClassificationLevel,
    Correction,
    Document,
)
from koipa.repositories import (
    AuditRepo,
    ClassifyRepo,
    LlmUsageRepo,
    SynthRepo,
    TrainingRepo,
)
from koipa.services.audit_chain import parse_chained_hash

pytestmark = pytest.mark.fullstack


def _require_pg() -> None:
    # [2026-10-12] 종전엔 이 파일만의 engine.connect() 직접 호출(타임아웃 없음)이었다 —
    # 2026-10-11에 7개 파일에서 고친 것과 같은 패턴(_pg_probe 미사용 복붙)이 여기 하나
    # 더 있었다. postgres_available()은 소켓 1초 타임아웃이 있어 PG가 내려간 채 이름
    # 해석이 멈추는 함정(localhost IPv6)을 피한다 — 공용 도구로 교체.
    if not postgres_available():
        pytest.skip("Postgres not reachable - start docker compose up -d postgres")


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def db():
    """rollback fixture — 각 테스트 종료 시 모든 변경 폐기."""
    _require_pg()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def document(db) -> Document:
    doc = Document(
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
    def test_lookup_helpers(self, db, document):
        repo = ClassifyRepo(db)
        assert repo.document_exists(document.doc_id) is True
        assert repo.document_exists(uuid.uuid4()) is False
        assert repo.level_id_by_code("TS") is not None
        assert repo.level_id_by_code("NOPE") is None

    def test_create_classification(self, db, document, levels):
        repo = ClassifyRepo(db)
        cls = repo.create_classification(
            doc_id=document.doc_id,
            model_version="v-test",
            predicted_level_id=levels["S1"],
            confidence=0.92,
            alternatives=[{"level_code": "TS", "confidence": 0.05}],
            chunk_count=3,
        )
        assert cls.classification_id is not None
        assert cls.status == "staging"
        assert cls.initial_status == "staging"
        assert float(cls.confidence) == pytest.approx(0.92, abs=1e-4)

    def test_initial_status_frozen_after_status_update(self, db, document, levels):
        """initial_status는 최초 게이트 판정을 동결 — 이후 status 변경(확정/교정)에 안 따라간다.

        회귀 대상: 대시보드 auto_confirmed가 status='staging'만 세면 사람이 확인할수록
        (staging→confirmed) 분자가 줄어드는 결함이 있었다. initial_status는 그 확인
        이후에도 최초 판정 그대로 남아야 집계가 정확해진다.
        """
        repo = ClassifyRepo(db)
        cls = repo.create_classification(
            doc_id=document.doc_id,
            model_version="v-test",
            predicted_level_id=levels["S1"],
            confidence=0.92,
            alternatives=[],
            status="staging",
        )
        assert cls.initial_status == "staging"
        repo.update_status(cls.classification_id, "confirmed")
        db.flush()
        refetched = repo.get(cls.classification_id)
        assert refetched.status == "confirmed"
        assert refetched.initial_status == "staging"

    def test_evidence_round_trip_from_spans(self, db, document, levels):
        from koipa.schemas.classify import EvidenceSpan

        repo = ClassifyRepo(db)
        cls = repo.create_classification(
            doc_id=document.doc_id,
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

        from koipa.db.models import ClassificationEvidence

        evs = (
            db.query(ClassificationEvidence)
            .filter_by(classification_id=cls.classification_id)
            .all()
        )
        assert len(evs) == 2

    def test_evidence_does_not_overflow_real_numeric_column(self, db, document, levels):
        """2026-10-12 KL 실운영 로그: psycopg.errors.NumericValueOutOfRange로 근거 INSERT가
        통째로 실패했었다. 지금까지의 테스트는 전부 MagicMock DB라 실제 Postgres의
        Numeric(4,3) 제약을 통과한 적이 없었다 — 이게 실제 제약에 부딫혀보는 첫 테스트다.

        1) 원래 버그를 그대로 재현하는 값(과거엔 여기서 NumericValueOutOfRange가 났다)이
           classify_repo.py의 2차 방어(클램프)만으로도 실제 DB에 안전하게 들어가는지.
        2) m3_labeling/pipeline.py._evidence_contribution()이 애초에 만들어내는 값(극단적인
           count)도 실제 DB에 그대로 들어가는지 — 클램프가 동작할 필요조차 없어야 한다.
        """
        from koipa.modules.m3_labeling.pipeline import _evidence_contribution
        from koipa.schemas.classify import EvidenceSpan

        repo = ClassifyRepo(db)
        cls = repo.create_classification(
            doc_id=document.doc_id,
            model_version="v",
            predicted_level_id=levels["TS"],
            confidence=0.85,
            alternatives=[],
        )

        # (1) 수정 전 코드가 그대로 DB에 보냈을 값 — count=2 * weight=9.99.
        pre_fix_overflow_value = 19.98
        # (2) 지금 공식이 극단적인 count(=100만 회 매칭)에서 실제로 내놓는 값.
        extreme_count_value = _evidence_contribution(1_000_000, 9.99)
        # count가 이 정도로 크면 round(...,3)이 weight와 '정확히 같게' 반올림할 수 있다
        # (test_evidence_contribution_bound.py에서 이미 증명한 성질) — 그래도 9.999는 못 넘는다.
        assert extreme_count_value <= 9.99

        spans = [
            EvidenceSpan(start=0, end=4, text="과거버그재현", weight=pre_fix_overflow_value),
            EvidenceSpan(start=5, end=9, text="극단count", weight=extreme_count_value),
        ]
        added = repo.add_evidence_from_spans(
            cls.classification_id, spans=spans, default_chunk_id=uuid.uuid4()
        )
        db.flush()  # 실제 INSERT가 여기서 나간다 — 터진다면 여기서 OperationalError/DataError

        assert added == 2
        from koipa.db.models import ClassificationEvidence

        evs = (
            db.query(ClassificationEvidence)
            .filter_by(classification_id=cls.classification_id)
            .order_by(ClassificationEvidence.excerpt_start)
            .all()
        )
        assert len(evs) == 2
        assert float(evs[0].contribution) == pytest.approx(9.999, abs=1e-3)  # 클램프됨
        assert float(evs[1].contribution) == pytest.approx(extreme_count_value, abs=1e-3)

    def test_correction_direction_underclass(self, db, document, levels):
        """원본 S2(3) → 보정 TS(1): order 감소 = underclass (보안 미탐)."""
        repo = ClassifyRepo(db)
        cls = repo.create_classification(
            doc_id=document.doc_id,
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

    def test_correction_direction_overclass_and_confirm(self, db, document, levels):
        repo = ClassifyRepo(db)
        cls = repo.create_classification(
            doc_id=document.doc_id,
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

    def test_mark_corrections_consumed(self, db, document, levels):
        repo = ClassifyRepo(db)
        cls = repo.create_classification(
            doc_id=document.doc_id,
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
            hyperparameters={"lr": 2e-5, "epochs": 5},
            trigger_type="manual",
        )
        assert run.status == "queued"

        repo.mark_started(run.run_id)
        db.flush()
        run_refreshed = repo.get_run(run.run_id)
        assert run_refreshed.status == "running"
        assert run_refreshed.started_at is not None

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

# ============================================================
# SynthRepo
# ============================================================

class TestSynthRepo:
    def test_sample_lifecycle(self, db, levels):
        repo = SynthRepo(db)
        sd = repo.create_sample(
            target_level_id=levels["S1"],
            llm_provider="anthropic",
            llm_model="claude-sonnet-4-6",
            generated_content="합성 본문 ...",
            domain_code="tech",
            doc_type="기술도면",
            quality_score=0.88,
        )
        assert sd.review_status == "pending_review"
        assert sd.domain_code == "tech"
        assert sd.doc_type == "기술도면"

        # list_pending_review 는 limit=50 · created_at 오름차순이다. 공유 DB 에 pending 이
        # 50건 넘게 쌓이면 **방금 만든 샘플이 가장 뒤라 상위 50건 밖으로 밀려난다**
        # (실측 2026-08-12: pending 55건 상태에서 이 단언이 깨졌다 — 코드 결함이 아니라
        # 테스트가 공유 DB 누적을 고려하지 않은 것이다).
        # 조회 자체가 동작하는지와, 내가 만든 건이 pending 으로 잡히는지를 나눠 본다.
        assert repo.list_pending_review(), "pending 조회가 비어 있다"
        mine = repo.list_by_status(
            "pending_review", limit=repo.count_pending_review() or 1
        )
        assert any(s.sample_id == sd.sample_id for s in mine)

        reviewed = repo.review(sd.sample_id, approved=True, reviewed_by="qa@test")
        assert reviewed is not None
        assert reviewed.review_status == "approved"
        assert reviewed.reviewed_at is not None

    def test_review_rejection(self, db, levels):
        repo = SynthRepo(db)
        sd = repo.create_sample(
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
        # Direct repository writes join the append-only audit chain.  The
        # column stores the compact ``previous-link:current-link`` format,
        # not a standalone SHA-256 digest.
        prev, current = parse_chained_hash(a.payload_hash)
        assert len(prev) == 16
        assert len(current) == 32

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
    def test_record_and_aggregate(self, db):
        repo = LlmUsageRepo(db)
        repo.record(
            provider="anthropic",
            model="claude-sonnet-4-6",
            purpose="sample_generation",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.01,
            billing_phase="development",
        )
        repo.record(
            provider="anthropic",
            model="claude-sonnet-4-6",
            purpose="sample_generation",
            input_tokens=2000,
            output_tokens=800,
            cost_usd=0.02,
            billing_phase="development",
        )
        db.flush()
        # rollback fixture라 같은 트랜잭션 내에서만 보임
        in_tokens, out_tokens = repo.total_tokens(billing_phase="development")
        assert in_tokens >= 3000
        assert out_tokens >= 1300
        cost = repo.total_cost_usd(billing_phase="development")
        assert cost >= 0.029  # 0.01 + 0.02 (부동소수 오차 여유)
