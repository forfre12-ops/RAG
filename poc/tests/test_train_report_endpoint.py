"""GET /train/models/{version_label}/report — 모델 성능 리포트(KL 요구사항 추적표 6절).

trainer.py 는 정확도·정밀도·재현율·F1·혼동행렬을 이미 계산해 ModelVersion.metrics(DB)에
저장하고 있었다 — 이걸 API 로 꺼내는 엔드포인트가 없었을 뿐이다. DB 없이(Postgres 미기동
환경) 검증하기 위해 TrainingRepo 를 가짜로 바꿔 끼운다(test_train_progress_wiring.py 와
같은 방식).
"""
from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest

from koipa.services.training_service import TrainingService


def _mv(label, **kw):
    base = dict(
        version_label=label, base_model="kf-deberta-base", trained_at=None,
        training_data_count=1000, is_active=False, metrics={},
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _FakeRepo:
    def __init__(self, db, *, by_label=None, active=None):
        self._by_label = by_label or {}
        self._active = active

    def get_by_label(self, version_label):
        return self._by_label.get(version_label)

    def get_active(self):
        return self._active


def _svc(monkeypatch, *, by_label=None, active=None):
    monkeypatch.setattr(
        "koipa.services.training_service.session_scope",
        lambda: contextlib.nullcontext(object()),
    )
    monkeypatch.setattr(
        "koipa.services.training_service.TrainingRepo",
        lambda db: _FakeRepo(db, by_label=by_label, active=active),
    )
    return TrainingService.__new__(TrainingService)


def test_unknown_version_returns_none(monkeypatch):
    svc = _svc(monkeypatch, by_label={})
    assert svc.report("v-missing") is None


def test_report_surfaces_stored_metrics(monkeypatch):
    mv = _mv("v-abc123", metrics={
        "accuracy": 0.91, "precision_macro": 0.88, "recall_macro": 0.85,
        "f1_macro": 0.865, "fnr_overall": 0.034, "fnr_by_grade": {"TS": 0.01, "S1": 0.05},
        "fnr_high": 0.02, "confusion_matrix": [[10, 1], [2, 9]],
        "classification_report": "...",
        "learning_curve": [
            {"epoch": 0.5, "step": 10, "loss": 0.82},
            {"epoch": 1.0, "step": 20, "eval_loss": 0.61},
        ],
    })
    svc = _svc(monkeypatch, by_label={"v-abc123": mv}, active=None)
    rep = svc.report("v-abc123")
    assert rep is not None
    assert rep.version_label == "v-abc123"
    assert rep.accuracy == 0.91
    assert rep.f1_macro == 0.865
    assert rep.fnr_by_grade == {"TS": 0.01, "S1": 0.05}
    assert rep.confusion_matrix == [[10, 1], [2, 9]]
    assert rep.learning_curve_status == "recorded"
    assert rep.learning_curve[0].train_loss == 0.82
    assert rep.learning_curve[1].eval_loss == 0.61
    assert rep.compared_to_active is None  # 활성 모델 없음 — 비교 불가


def test_report_compares_against_active_model(monkeypatch):
    """파인튜닝 전후 비교(요구사항 6절 "모델 성능 비교") — 활성 모델이 비교 기준."""
    candidate = _mv("v-new", metrics={"accuracy": 0.93, "f1_macro": 0.90, "fnr_overall": 0.02})
    active = _mv("v-old", is_active=True, metrics={"accuracy": 0.91, "f1_macro": 0.865, "fnr_overall": 0.034})
    svc = _svc(monkeypatch, by_label={"v-new": candidate, "v-old": active}, active=active)

    rep = svc.report("v-new")
    assert rep.compared_to_active is not None
    c = rep.compared_to_active
    assert c.version_label == "v-old"
    assert c.accuracy_delta == pytest.approx(0.02)       # 0.93 - 0.91
    assert c.f1_macro_delta == pytest.approx(0.035)       # 0.90 - 0.865
    assert c.fnr_overall_delta == pytest.approx(-0.014)   # 미탐 감소(개선) → 음수


def test_report_on_the_active_model_itself_has_no_comparison(monkeypatch):
    """자기 자신과 비교하지 않는다 — 활성 모델 리포트를 조회하면 compared_to_active=None."""
    active = _mv("v-current", is_active=True, metrics={"accuracy": 0.9})
    svc = _svc(monkeypatch, by_label={"v-current": active}, active=active)
    rep = svc.report("v-current")
    assert rep.compared_to_active is None


def test_missing_metric_fields_do_not_crash(monkeypatch):
    """metrics dict 에 일부 키가 없어도(옛 모델 등) 500 으로 죽지 않는다 — None 으로 채운다."""
    mv = _mv("v-sparse", metrics={"accuracy": 0.8})  # f1_macro 등 없음
    svc = _svc(monkeypatch, by_label={"v-sparse": mv})
    rep = svc.report("v-sparse")
    assert rep.accuracy == 0.8
    assert rep.f1_macro is None
    assert rep.fnr_by_grade == {}
    assert rep.confusion_matrix == []
    assert rep.learning_curve == [] and rep.learning_curve_status == "not_recorded"


def test_report_surfaces_accuracy_curve(monkeypatch):
    """[2026-10-11] 에폭별 accuracy도 learning_curve에 그대로 실린다(KL 요구사항 6절)."""
    mv = _mv("v-acc", metrics={
        "accuracy": 0.9,
        "learning_curve": [
            {"epoch": 1.0, "step": 10, "eval_accuracy": 0.7},
            {"epoch": 2.0, "step": 20, "eval_accuracy": 0.85},
        ],
    })
    svc = _svc(monkeypatch, by_label={"v-acc": mv})
    rep = svc.report("v-acc")
    assert [p.accuracy for p in rep.learning_curve] == [0.7, 0.85]


def test_pdf_accuracy_chart_present_when_recorded_absent_when_not():
    """[2026-10-11] accuracy 이력이 있으면 그리고, 없으면 추정 없이 None(=미기록 문구로 대체)."""
    from reportlab.lib import colors

    from koipa.api._training_report_pdf import _curve_drawing

    series = (("정확도", "accuracy", colors.HexColor("#16a34a")),)

    with_data = [SimpleNamespace(accuracy=0.7), SimpleNamespace(accuracy=0.85)]
    assert _curve_drawing(with_data, font_name="Helvetica", title="x", series_spec=series) is not None

    without_data = [SimpleNamespace(accuracy=None), SimpleNamespace(accuracy=None)]
    assert _curve_drawing(without_data, font_name="Helvetica", title="x", series_spec=series) is None


def test_pdf_renderer_uses_stored_values_and_is_a_valid_pdf(monkeypatch):
    """PDF는 빈 과거 곡선을 추정하지 않고, 저장된 지표만 읽어야 한다."""
    mv = _mv("v-pdf", metrics={
        "accuracy": 0.91, "f1_macro": 0.88, "fnr_overall": 0.03,
        "fnr_by_grade": {"TS": 0.01, "S1": 0.02},
        "learning_curve": [{"epoch": 1, "step": 20, "loss": 0.8}, {"epoch": 1, "step": 20, "eval_loss": 0.6}],
    })
    rep = _svc(monkeypatch, by_label={"v-pdf": mv}).report("v-pdf")
    from koipa.api._training_report_pdf import render_training_report_pdf

    payload = render_training_report_pdf(rep)

    assert payload.startswith(b"%PDF-")
    assert len(payload) > 1000


def test_trainer_loss_log_projection_keeps_only_finite_loss_and_accuracy_values():
    """[2026-10-11] eval_accuracy도 남긴다(KL 요구사항 6절 Epoch별 Accuracy 그래프).

    종전엔 accuracy만 있고 loss가 없는 행(4번째)을 "손실 없음"으로 버렸다 — 이제는
    accuracy 자체도 보존 대상이라 세 값(train_loss·eval_loss·accuracy) 중 하나라도
    유한하면 남긴다.
    """
    from koipa.modules.m4_training.trainer import _learning_curve_from_log_history

    curve = _learning_curve_from_log_history([
        {"epoch": 0.5, "step": 10, "loss": 0.9, "learning_rate": 0.00002},
        {"epoch": 1.0, "step": 20, "eval_loss": 0.6, "eval_runtime": 3.2},
        {"epoch": 1.5, "step": 30, "loss": float("nan")},
        {"epoch": 2.0, "step": 40, "eval_accuracy": 0.9},
    ])

    assert curve == [
        {"epoch": 0.5, "step": 10, "train_loss": 0.9, "eval_loss": None, "accuracy": None},
        {"epoch": 1.0, "step": 20, "train_loss": None, "eval_loss": 0.6, "accuracy": None},
        {"epoch": 2.0, "step": 40, "train_loss": None, "eval_loss": None, "accuracy": 0.9},
    ]


def test_csv_export_route_exists_and_is_registered():
    """format=csv 라우트가 실제로 등록돼 있는지(배선 확인, DB 불필요)."""
    from koipa.api.training import router
    import inspect

    paths = {r.path for r in router.routes}
    assert "/train/models/{version_label}/report" in paths
    endpoint = next(route.endpoint for route in router.routes if route.path == "/train/models/{version_label}/report")
    assert "json|csv|pdf" in inspect.getsource(endpoint)
