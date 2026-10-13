"""운영 화면의 성능 수치가 무엇의 시각인지 숨기지 않는 계약."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from koipa.services.metrics_service import MetricsService
from koipa.services.training_service import _report_metrics


def _model(*, metrics: dict, trained_at: datetime | None = None):
    """_from_stored에 필요한 ModelVersion 표면만 만든다(DB 없이 검증)."""
    return SimpleNamespace(
        version_label="v-test",
        metrics=metrics,
        trained_at=trained_at,
        training_data_count=0,
    )


def test_stored_metrics_call_out_evaluation_time_when_present():
    report = MetricsService._from_stored(
        _model(
            metrics={"evaluated_at": "2026-10-11T01:02:03+00:00", "sample_count": 40},
            trained_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
        )
    )

    assert report.measured_at == "2026-10-11T01:02:03+00:00"
    assert report.evaluated_at == report.measured_at
    assert report.trained_at == "2026-10-10T00:00:00+00:00"
    assert report.metric_timestamp_source == "evaluation"
    assert report.fnr_warning_threshold == 0.05


def test_legacy_stored_metrics_admit_they_only_have_training_time():
    report = MetricsService._from_stored(
        _model(metrics={}, trained_at=datetime(2026, 10, 10, tzinfo=timezone.utc))
    )

    assert report.measured_at == "2026-10-10T00:00:00+00:00"
    assert report.evaluated_at is None
    assert report.metric_timestamp_source == "training_fallback"


def test_training_metrics_keep_existing_evaluation_time_without_relabeling_legacy_data():
    given = "2026-10-09T12:00:00+00:00"
    assert _report_metrics({"evaluated_at": given})["evaluated_at"] == given
    assert "evaluated_at" not in _report_metrics({})
