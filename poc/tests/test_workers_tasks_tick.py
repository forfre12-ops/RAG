"""A5: active_learning_tick + drift_tick 단위 검증.

DB·Celery broker 없이 동작. evaluate_retraining_need / run_drift_check를 stub해서
tick 함수의 분기(URGENT/RECOMMENDED/OK · weekly window · enqueue 실패 폴백)만 검증.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from lloydk.modules.m6_evaluation.active_learning import ActiveLearningStatus
from lloydk.services.drift_monitor import DriftReport
from lloydk.workers.tasks import active_learning_tick, drift_tick


def _status(retrain="OK", reason="x", underclass=0, total=0):
    return ActiveLearningStatus(
        unconsumed_total=total,
        pending_underclass=underclass,
        retrain_status=retrain,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# active_learning_tick — mode별 분기
# ---------------------------------------------------------------------------


def test_tick_dry_mode_returns_status_no_enqueue():
    with patch("lloydk.modules.m6_evaluation.active_learning.evaluate_retraining_need",
               return_value=_status(retrain="URGENT_RETRAIN", underclass=20)):
        with patch("lloydk.workers.tasks.train_classifier_task.apply_async") as enq:
            out = active_learning_tick(mode="dry")
    assert out["mode"] == "dry"
    assert "triggered" not in out  # dry는 enqueue 안 함
    assert enq.call_count == 0


def test_tick_snapshot_mode_returns_status_no_enqueue():
    with patch("lloydk.modules.m6_evaluation.active_learning.evaluate_retraining_need",
               return_value=_status(retrain="URGENT_RETRAIN", underclass=20)):
        with patch("lloydk.workers.tasks.train_classifier_task.apply_async") as enq:
            out = active_learning_tick(mode="snapshot")
    assert out["mode"] == "snapshot"
    assert "triggered" not in out
    assert enq.call_count == 0


def test_tick_auto_urgent_enqueues_train():
    with patch("lloydk.modules.m6_evaluation.active_learning.evaluate_retraining_need",
               return_value=_status(retrain="URGENT_RETRAIN", underclass=20,
                                     reason="pending_underclass=20 >= urgent=10")):
        with patch("lloydk.workers.tasks.train_classifier_task.apply_async") as enq:
            out = active_learning_tick(mode="auto")
    assert out["triggered"] == "URGENT_RETRAIN"
    assert enq.call_count == 1


def test_tick_auto_urgent_enqueue_failure_marked():
    with patch("lloydk.modules.m6_evaluation.active_learning.evaluate_retraining_need",
               return_value=_status(retrain="URGENT_RETRAIN", underclass=20)):
        with patch("lloydk.workers.tasks.train_classifier_task.apply_async",
                   side_effect=RuntimeError("broker down")):
            out = active_learning_tick(mode="auto")
    assert out["triggered"] == "ENQUEUE_FAILED"


def test_tick_auto_recommended_outside_window_skips():
    """RECOMMENDED 상태 + 월요일 03~05시 아닌 시각 → SKIP_WAIT_WEEKLY_WINDOW.

    tasks.active_learning_tick은 함수 안에서 from datetime import datetime을 호출하므로
    datetime.datetime 자체를 패치한다.
    """
    fixed = datetime(2026, 6, 2, 14, 0, 0)  # 화요일 14시 (weekday=1)

    class FakeDateTime:
        @classmethod
        def utcnow(cls):
            return fixed

    import datetime as _dt
    with patch.object(_dt, "datetime", FakeDateTime):
        with patch("lloydk.modules.m6_evaluation.active_learning.evaluate_retraining_need",
                   return_value=_status(retrain="RETRAIN_RECOMMENDED", total=60)):
            with patch("lloydk.workers.tasks.train_classifier_task.apply_async") as enq:
                out = active_learning_tick(mode="auto")
    assert out["triggered"] == "SKIP_WAIT_WEEKLY_WINDOW"
    assert enq.call_count == 0


def test_tick_auto_recommended_inside_window_enqueues():
    """월요일 04시 → enqueue."""
    fixed = datetime(2026, 6, 1, 4, 0, 0)  # 2026-06-01 = 월요일
    assert fixed.weekday() == 0

    class FakeDateTime:
        @classmethod
        def utcnow(cls):
            return fixed

    import datetime as _dt
    with patch.object(_dt, "datetime", FakeDateTime):
        with patch("lloydk.modules.m6_evaluation.active_learning.evaluate_retraining_need",
                   return_value=_status(retrain="RETRAIN_RECOMMENDED", total=60)):
            with patch("lloydk.workers.tasks.train_classifier_task.apply_async") as enq:
                out = active_learning_tick(mode="auto")
    assert out["triggered"] == "RETRAIN_RECOMMENDED"
    assert enq.call_count == 1


def test_tick_auto_ok_does_nothing():
    with patch("lloydk.modules.m6_evaluation.active_learning.evaluate_retraining_need",
               return_value=_status(retrain="OK")):
        with patch("lloydk.workers.tasks.train_classifier_task.apply_async") as enq:
            out = active_learning_tick(mode="auto")
    assert out["triggered"] == "NONE"
    assert enq.call_count == 0


# ---------------------------------------------------------------------------
# drift_tick — run_drift_check 결과 그대로 dict로 반환
# ---------------------------------------------------------------------------


def test_drift_tick_returns_report_dict():
    report = DriftReport(
        sample_size=10, cosine_mean=0.5, cosine_std=0.1,
        kl_divergence=0.2, alert=False, threshold_alert=0.5,
    )
    with patch("lloydk.services.drift_monitor.run_drift_check", return_value=report):
        out = drift_tick(limit=10, threshold=0.5)
    assert out["sample_size"] == 10
    assert out["kl_divergence"] == 0.2
    assert out["alert"] is False


def test_drift_tick_alert_passthrough():
    report = DriftReport(
        sample_size=200, cosine_mean=0.1, cosine_std=0.05,
        kl_divergence=0.8, alert=True, threshold_alert=0.5,
    )
    with patch("lloydk.services.drift_monitor.run_drift_check", return_value=report):
        out = drift_tick()
    assert out["alert"] is True
