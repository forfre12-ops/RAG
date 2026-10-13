"""M6 평가/능동학습 모듈 + MetricsService 결합 테스트."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from koipa.db import SessionLocal, engine, session_scope
from koipa.db.models import (
    Classification,
    ClassificationLevel,
    Correction,
    Document,
    ModelVersion,
)
from koipa.modules.m6_evaluation import (
    build_confusion_matrix,
    compute_metrics_from_arrays,
    compute_metrics_from_db,
    detect_underclass_alarms,
    evaluate_retraining_need,
    render_confusion_matrix_png,
    render_html_report,
)
from koipa.modules.m6_evaluation.active_learning import consume_corrections_for_run


def _pg_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False
    except Exception:  # noqa: BLE001
        return False


def _require_pg() -> None:
    if not _pg_ok():
        pytest.skip("Postgres not reachable")


# ============================================================
# Pure function tests (no DB)
# ============================================================

class TestMetricsFromArrays:
    def test_perfect_accuracy(self):
        y = ["TS", "S1", "S2", "S3"]
        m = compute_metrics_from_arrays(y, y)
        assert m.accuracy == 1.0
        assert m.f1_macro == 1.0
        assert m.fnr_overall == 0.0
        assert all(v == 0.0 for v in m.fnr_by_grade.values())
        assert m.sample_count == 4

    def test_partial_with_underclass(self):
        # TS 1건이 S3로 미탐, S1·S2·S3는 정상
        y_true = ["TS", "TS", "S1", "S2", "S3", "S3"]
        y_pred = ["TS", "S3", "S1", "S2", "S3", "S3"]
        m = compute_metrics_from_arrays(y_true, y_pred)
        assert m.sample_count == 6
        assert m.fnr_by_grade["TS"] == 0.5
        assert m.fnr_by_grade["S1"] == 0.0
        assert m.fnr_overall == pytest.approx(1 / 6, abs=1e-4)

    def test_empty_returns_zero_result(self):
        m = compute_metrics_from_arrays([], [])
        assert m.sample_count == 0
        assert m.accuracy == 0.0
        assert m.confusion_matrix == []

    def test_mismatched_length_raises(self):
        # [M-metrics-len] 계약 갱신: 옛 로직은 길이 불일치를 조용히 all-zeros(sample_count=0)로
        # 반환해 데이터/파이프라인 버그를 은폐했다(FNR=0.0 → 미탐 회귀를 게이트가 못 잡음).
        # 이제 ValueError를 raise한다. 안전(fail-SECURE)을 위해 옛 계약으로 되돌리지 않는다.
        with pytest.raises(ValueError):
            compute_metrics_from_arrays(["TS"], ["TS", "S1"])


class TestConfusionMatrix:
    def test_critical_alarm_for_ts_to_s3(self):
        y_true = ["TS"]
        y_pred = ["S3"]
        cm = build_confusion_matrix(y_true, y_pred)
        assert len(cm.alarms) == 1
        a = cm.alarms[0]
        assert a.truth == "TS"
        assert a.pred == "S3"
        assert a.severity == "critical"
        assert cm.total_underclass == 1

    def test_no_alarm_for_overclass(self):
        # 정답 S3 → 예측 TS (과탐, 사용자 불편이지 보안 미탐 아님)
        y_true = ["S3"] * 5
        y_pred = ["TS"] * 5
        cm = build_confusion_matrix(y_true, y_pred)
        assert cm.alarms == []
        assert cm.total_underclass == 0

    def test_severity_order(self):
        # TS→S3 critical, TS→S2 high, TS→S1 medium
        y_true = ["TS"] * 30
        y_pred = ["S3", "S2", "S1"] * 10  # 각 10건씩
        cm = build_confusion_matrix(y_true, y_pred, alarm_rate_threshold=0.0, alarm_count_threshold=1)
        severities = [a.severity for a in cm.alarms]
        # critical이 가장 먼저
        assert severities[0] == "critical"
        assert "high" in severities
        assert "medium" in severities

    def test_rate_threshold_filters_below(self):
        # TS 100건 중 S1 1건만 미탐 → rate=1% < 기본 5%
        # 단 critical은 1건이라도 알람이므로 medium severity는 필터됨
        y_true = ["TS"] * 100
        y_pred = ["TS"] * 99 + ["S1"]
        cm = build_confusion_matrix(y_true, y_pred, alarm_rate_threshold=0.05)
        # TS→S1은 medium → 비율 1%로 필터링되어야 함
        med_alarms = [a for a in cm.alarms if a.severity == "medium"]
        assert med_alarms == []

    def test_detect_underclass_directly(self):
        import numpy as np
        cm = np.array([
            [3, 0, 0, 2],  # TS: 2건이 S3로 미탐
            [0, 5, 0, 0],
            [0, 0, 4, 0],
            [0, 0, 0, 6],
        ])
        alarms = detect_underclass_alarms(cm, labels=["TS", "S1", "S2", "S3"])
        assert any(a.truth == "TS" and a.pred == "S3" and a.severity == "critical" for a in alarms)


class TestReport:
    def test_render_cm_png(self):
        y_true = ["TS", "S1", "S2", "S3"] * 5
        y_pred = ["TS", "S1", "S2", "S3"] * 5
        cm = build_confusion_matrix(y_true, y_pred)
        png = render_confusion_matrix_png(cm)
        assert isinstance(png, bytes)
        # PNG signature
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(png) > 200  # 최소 크기

    def test_render_html_includes_kpis_and_alarms(self):
        y_true = ["TS", "TS", "S1", "S2", "S3"]
        y_pred = ["TS", "S3", "S1", "S2", "S3"]  # TS→S3 critical
        m = compute_metrics_from_arrays(y_true, y_pred, model_version="v-test-1")
        cm = build_confusion_matrix(y_true, y_pred)
        html = render_html_report(m, cm, measured_at="2026-05-27")
        assert "v-test-1" in html
        assert "Accuracy" in html
        assert "FNR overall" in html
        assert "CRITICAL" in html or "critical" in html
        assert "data:image/png;base64," in html or "CM 시각화 사용 불가" in html


# ============================================================
# DB-backed tests
# ============================================================

def db_backed(obj):
    obj = pytest.mark.fullstack(obj)
    return pytest.mark.usefixtures("require_pg")(obj)


@pytest.fixture
def require_pg():
    _require_pg()


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def levels(db):
    return {lv.level_code: lv.level_id for lv in db.query(ClassificationLevel).all()}


def _seed_classification(db, levels: dict, model_version: str, code: str) -> Classification:
    doc = Document(filename="x.pdf", source_format="pdf")
    db.add(doc)
    db.flush()
    cls = Classification(
        doc_id=doc.doc_id,
        model_version=model_version,
        predicted_level_id=levels[code],
        confidence=0.9,
        alternatives=[],
        status="confirmed",  # admission 게이트(finalized만 학습/트리거 카운트) 통과용
    )
    db.add(cls)
    db.flush()
    return cls


@db_backed
class TestMetricsFromDB:
    def test_no_classifications_returns_none(self):
        result = compute_metrics_from_db(f"v-unseen-{uuid.uuid4().hex[:6]}")
        assert result is None

    def test_with_classifications_no_corrections(self, db, levels):
        version = f"v-m6-{uuid.uuid4().hex[:6]}"
        for code in ["TS", "TS", "S1", "S2", "S3"]:
            _seed_classification(db, levels, version, code)
        db.commit()  # 다른 세션에서 보이게

        try:
            result = compute_metrics_from_db(version)
            assert result is not None
            assert result.sample_count == 5
            # 보정 없음 → predicted가 정답으로 간주 → accuracy=1.0
            assert result.accuracy == 1.0
            assert result.fnr_overall == 0.0
        finally:
            # cleanup
            with session_scope() as s:
                s.query(Classification).filter_by(model_version=version).delete()

    def test_with_correction_drops_accuracy(self, db, levels):
        version = f"v-m6c-{uuid.uuid4().hex[:6]}"
        # 정답 TS인데 모델은 S3으로 분류 → 보정으로 'TS'로 수정 → underclass
        cls = _seed_classification(db, levels, version, "S3")
        db.add(
            Correction(
                classification_id=cls.classification_id,
                original_level_id=levels["S3"],
                corrected_level_id=levels["TS"],
                direction="underclass",
                corrected_by="qa@test",
            )
        )
        db.commit()

        try:
            result = compute_metrics_from_db(version)
            assert result is not None
            assert result.sample_count == 1
            # truth=TS, pred=S3 → 정확히 미탐 1건
            assert result.accuracy == 0.0
            assert result.fnr_by_grade["TS"] == 1.0
        finally:
            with session_scope() as s:
                ids = [c.classification_id for c in s.query(Classification).filter_by(model_version=version).all()]
                if ids:
                    s.query(Correction).filter(Correction.classification_id.in_(ids)).delete(synchronize_session=False)
                    s.query(Classification).filter_by(model_version=version).delete()


@db_backed
class TestActiveLearning:
    def test_status_ok_when_no_pending(self):
        # 다른 테스트의 pending이 있을 수 있어 절대 조건은 아님
        status = evaluate_retraining_need()
        assert status.retrain_status in {"OK", "RETRAIN_RECOMMENDED", "URGENT_RETRAIN"}

    def test_urgent_when_underclass_exceeds_threshold(self, db, levels):
        version = f"v-al-{uuid.uuid4().hex[:6]}"
        ids = []
        for _ in range(12):
            cls = _seed_classification(db, levels, version, "S3")
            db.add(
                Correction(
                    classification_id=cls.classification_id,
                    original_level_id=levels["S3"],
                    corrected_level_id=levels["TS"],
                    direction="underclass",
                    corrected_by="qa",
                )
            )
            db.flush()
            ids.append(cls.classification_id)
        db.commit()
        try:
            status = evaluate_retraining_need(urgent_underclass_threshold=10)
            assert status.pending_underclass >= 12
            assert status.retrain_status == "URGENT_RETRAIN"
        finally:
            with session_scope() as s:
                s.query(Correction).filter(Correction.classification_id.in_(ids)).delete(synchronize_session=False)
                s.query(Classification).filter_by(model_version=version).delete()

    def test_consume_marks_corrections(self, db, levels):
        from koipa.repositories import TrainingRepo

        version = f"v-cons-{uuid.uuid4().hex[:6]}"
        cls = _seed_classification(db, levels, version, "S3")
        corr = Correction(
            classification_id=cls.classification_id,
            original_level_id=levels["S3"],
            corrected_level_id=levels["TS"],
            direction="underclass",
            corrected_by="qa",
        )
        db.add(corr)
        # 실제 TrainingRun 생성 (consumed_in_run FK 참조 대상)
        run = TrainingRepo(db).create_run(total_samples=1)
        db.commit()
        try:
            n = consume_corrections_for_run(run.run_id, correction_ids=[corr.correction_id])
            assert n == 1
            with session_scope() as s:
                refreshed = s.get(Correction, corr.correction_id)
                assert refreshed.consumed_in_run == run.run_id
                assert refreshed.consumed_at is not None
        finally:
            with session_scope() as s:
                s.query(Correction).filter_by(correction_id=corr.correction_id).delete()
                s.query(Classification).filter_by(model_version=version).delete()
                from koipa.db.models import TrainingRun as _TR  # noqa: PLC0415
                s.query(_TR).filter_by(run_id=run.run_id).delete()


@db_backed
class TestMetricsServiceLive:
    def test_active_model_with_stored_metrics_returns_them(self, db):
        from koipa.services.metrics_service import MetricsService

        version = f"v-svc-{uuid.uuid4().hex[:6]}"
        mv = ModelVersion(
            version_label=version,
            base_model="kf-deberta-base",
            metrics={
                "accuracy": 0.85,
                "f1_macro": 0.82,
                "fnr_overall": 0.04,
                "fnr_by_grade": {"TS": 0.02, "S1": 0.03},
            },
            training_data_count=500,
        )
        db.add(mv)
        db.flush()
        # 활성화
        from koipa.repositories import TrainingRepo
        TrainingRepo(db).activate_model_version(mv.version_id)
        db.commit()
        try:
            r = MetricsService().latest()
            assert r is not None
            assert r.model_version == version
            assert r.accuracy == pytest.approx(0.85)
            assert r.f1_macro == pytest.approx(0.82)
        finally:
            with session_scope() as s:
                s.query(ModelVersion).filter_by(version_id=mv.version_id).delete()

    def test_confusion_matrix_live_from_classifications(self, db, levels):
        from koipa.services.metrics_service import MetricsService

        version = f"v-cm-{uuid.uuid4().hex[:6]}"
        mv = ModelVersion(version_label=version, base_model="kf-deberta-base", metrics={})
        db.add(mv)
        db.flush()
        # 3건 분류 (보정 없음 → predicted=truth)
        for code in ["TS", "S1", "S2"]:
            _seed_classification(db, levels, version, code)
        db.commit()
        try:
            cm = MetricsService().confusion_matrix(version)
            assert cm is not None
            assert cm.model_version == version
            # 대각선 합 = 3 (각 등급 1건씩 정답)
            diag_sum = sum(cm.matrix[i][i] for i in range(len(cm.labels)))
            assert diag_sum == 3
        finally:
            with session_scope() as s:
                s.query(Classification).filter_by(model_version=version).delete()
                s.query(ModelVersion).filter_by(version_id=mv.version_id).delete()
