"""A5: active_learning_tick + drift_tick 단위 검증 (+ 작업 #34: 나머지 tick 태스크).

DB·Celery broker 없이 동작. evaluate_retraining_need / run_drift_check를 stub해서
tick 함수의 분기(URGENT/RECOMMENDED/OK · weekly window · enqueue 실패 폴백)만 검증.

작업 #34: auto_rollback_tick·deliver_outbox_tick·ensure_partitions_tick·train_classifier_task의
핵심 분기를 의존성 patch(mock)로 격리해 검증한다(DB/redis 불요).
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from lloydk.modules.m6_evaluation.active_learning import ActiveLearningStatus
from lloydk.services.drift_monitor import DriftReport
from lloydk.workers.tasks import (
    active_learning_tick,
    auto_rollback_tick,
    deliver_outbox_tick,
    drift_tick,
    ensure_partitions_tick,
    train_classifier_task,
)


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


def test_tick_auto_training_disabled_skips_enqueue():
    """[재학습 토폴로지 가드] enable_training=False(고객사) → URGENT여도 train enqueue 안 함.

    고객사(onprem-local)는 설계상 추론+비모수 전용 — 자동 재학습 루프가 돌면 안 된다(죽음의 나선
    고객사 차단). status 평가는 유지하되 triggered=SKIP_TRAINING_DISABLED로 조기 반환.
    """
    with patch("lloydk.config.settings.enable_training", False, create=True):
        with patch("lloydk.modules.m6_evaluation.active_learning.evaluate_retraining_need",
                   return_value=_status(retrain="URGENT_RETRAIN", underclass=20)):
            with patch("lloydk.workers.tasks.train_classifier_task.apply_async") as enq:
                out = active_learning_tick(mode="auto")
    assert out["triggered"] == "SKIP_TRAINING_DISABLED"
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


# ---------------------------------------------------------------------------
# 작업 #34 — auto_rollback_tick · deliver_outbox_tick · ensure_partitions_tick
#                · train_classifier_task (핵심 진입 분기)
# ---------------------------------------------------------------------------

# ---- auto_rollback_tick ---------------------------------------------------
# settings / evaluate_rollback_need / rollback_active_model 모두 함수 내부에서
# 원본 모듈을 from-import 하므로 원본 위치를 patch 한다.


def test_auto_rollback_tick_disabled_does_not_call_rollback():
    """기본(auto_rollback_enabled=False): should_rollback이어도 판정만, 롤백 미호출."""
    decision = {
        "should_rollback": True,
        "reason": "regression",
        "live_fnr_high": 0.30,
        "baseline_fnr_high": 0.10,
        "tolerance": 0.05,
    }
    with patch("lloydk.config.settings.auto_rollback_enabled", False, create=True):
        with patch(
            "lloydk.services.training_service.evaluate_rollback_need",
            return_value=decision,
        ):
            with patch(
                "lloydk.services.training_service.rollback_active_model"
            ) as rb:
                out = auto_rollback_tick()

    assert out["auto_rollback_enabled"] is False
    assert out["should_rollback"] is True  # 판정 결과는 그대로 전달
    assert "rollback" not in out  # 실제 롤백은 미수행
    assert rb.call_count == 0


def test_auto_rollback_tick_enabled_and_should_rollback_calls_rollback():
    """auto_rollback_enabled=True + should_rollback=True → rollback_active_model 호출."""
    decision = {
        "should_rollback": True,
        "reason": "regression",
        "live_fnr_high": 0.30,
        "baseline_fnr_high": 0.10,
        "tolerance": 0.05,
    }
    rollback_result = {"rolled_back": True, "to_version": "v2"}
    with patch("lloydk.config.settings.auto_rollback_enabled", True, create=True):
        with patch(
            "lloydk.services.training_service.evaluate_rollback_need",
            return_value=decision,
        ):
            with patch(
                "lloydk.services.training_service.rollback_active_model",
                return_value=rollback_result,
            ) as rb:
                out = auto_rollback_tick()

    assert out["auto_rollback_enabled"] is True
    assert out["rollback"] == rollback_result
    assert rb.call_count == 1


def test_auto_rollback_tick_enabled_but_no_regression_skips():
    """enabled=True여도 should_rollback=False면 롤백 미호출."""
    decision = {"should_rollback": False, "reason": "ok"}
    with patch("lloydk.config.settings.auto_rollback_enabled", True, create=True):
        with patch(
            "lloydk.services.training_service.evaluate_rollback_need",
            return_value=decision,
        ):
            with patch(
                "lloydk.services.training_service.rollback_active_model"
            ) as rb:
                out = auto_rollback_tick()

    assert out["auto_rollback_enabled"] is True
    assert out["should_rollback"] is False
    assert "rollback" not in out
    assert rb.call_count == 0


# ---- deliver_outbox_tick --------------------------------------------------
# deliver_once / get_outbox_store / http_send_via_httpx 모두 outbox 모듈에서
# from-import 하므로 원본 위치를 patch 한다.


def test_deliver_outbox_tick_dlq_warning_path_passthrough():
    """dlq>0 → warning 경로, deliver_once 반환 dict 그대로 전달."""
    result = {"sent": 1, "failed": 0, "dlq": 2, "ready": 3}
    with patch("lloydk.services.outbox.get_outbox_store", return_value=object()):
        with patch("lloydk.services.outbox.http_send_via_httpx", return_value=200):
            with patch(
                "lloydk.services.outbox.deliver_once", return_value=result
            ) as dl:
                out = deliver_outbox_tick(limit=7)

    assert out == result
    assert dl.call_count == 1
    # limit 인자가 deliver_once로 전달되는지 확인
    assert dl.call_args.kwargs.get("limit") == 7


def test_deliver_outbox_tick_info_path_passthrough():
    """dlq==0, sent/failed>0 → info 경로, 반환 dict 그대로 전달."""
    result = {"sent": 5, "failed": 1, "dlq": 0, "ready": 6}
    with patch("lloydk.services.outbox.get_outbox_store", return_value=object()):
        with patch("lloydk.services.outbox.http_send_via_httpx", return_value=200):
            with patch("lloydk.services.outbox.deliver_once", return_value=result):
                out = deliver_outbox_tick()

    assert out == result


def test_deliver_outbox_tick_idle_passthrough():
    """아무 것도 안 보낸 경우(sent/failed/dlq 모두 0)에도 반환 dict 전달."""
    result = {"sent": 0, "failed": 0, "dlq": 0, "ready": 0}
    with patch("lloydk.services.outbox.get_outbox_store", return_value=object()):
        with patch("lloydk.services.outbox.http_send_via_httpx", return_value=200):
            with patch("lloydk.services.outbox.deliver_once", return_value=result):
                out = deliver_outbox_tick()

    assert out == result


# ---- ensure_partitions_tick -----------------------------------------------
# ensure_partitions는 함수 내부에서 partitions 모듈에서 from-import 하므로 원본 patch.


def test_ensure_partitions_tick_failed_warning_passthrough():
    """failed가 있으면 warning 경로, 반환 dict 그대로 전달."""
    result = {"ensured": ["t_2026_06"], "failed": ["t_2026_07"]}
    with patch(
        "lloydk.services.partitions.ensure_partitions", return_value=result
    ) as ep:
        out = ensure_partitions_tick(months_ahead=2)

    assert out == result
    assert ep.call_count == 1
    assert ep.call_args.kwargs.get("months_ahead") == 2


def test_ensure_partitions_tick_all_ok_passthrough():
    """failed 없으면 경고 없이 반환 dict 그대로 전달."""
    result = {"ensured": ["t_2026_06", "t_2026_07"], "failed": []}
    with patch(
        "lloydk.services.partitions.ensure_partitions", return_value=result
    ):
        out = ensure_partitions_tick()

    assert out == result


# ---- train_classifier_task (핵심 진입 분기) -------------------------------
# 무거운 의존성(trainer/등록/소비)은 모두 patch. 교정 rebuild 행이 없고 TrainingRun도
# 미생성(run_uuid=None)인 경로 → train_classifier 호출 후 corrections_consumed=0 조기 반환.


def _empty_rebuild():
    return SimpleNamespace(rows=[], correction_ids=[], row_count=0)


def test_train_classifier_task_no_corrections_no_run_early_return():
    """교정 행 없음 + TrainingRun 미생성 → 학습은 수행, 소비 0으로 조기 반환."""
    fake_report = SimpleNamespace(
        __dict__={"f1": 0.9, "accuracy": 0.91},
        model_version=None,
    )
    with patch(
        "lloydk.modules.m6_evaluation.corrections_rebuild.build_labeled_rows_from_corrections",
        return_value=_empty_rebuild(),
    ):
        with patch(
            "lloydk.workers.tasks._create_training_run_guarded", return_value=None
        ):
            with patch(
                "lloydk.modules.m4_training.trainer.train_classifier",
                return_value=fake_report,
            ) as tc:
                with patch(
                    "lloydk.services.training_service.register_and_gate_model",
                    return_value={"registered": True},
                ):
                    with patch(
                        "lloydk.modules.m6_evaluation.active_learning.consume_corrections_for_run"
                    ) as consume:
                        out = train_classifier_task(spec_kwargs={})

    assert tc.call_count == 1
    assert out["corrections_incorporated"] == 0
    assert out["corrections_consumed"] == 0
    assert out["corrections_run_id"] is None
    assert out["deploy"] == {"registered": True}
    # 반영분이 없으므로 소비는 호출되지 않음(무손실 보류).
    assert consume.call_count == 0


def test_train_classifier_task_register_exception_falls_back():
    """register_and_gate_model 예외 → deploy 폴백 dict, 태스크는 계속 진행."""
    fake_report = SimpleNamespace(
        __dict__={"f1": 0.88},
        model_version=None,
    )
    with patch(
        "lloydk.modules.m6_evaluation.corrections_rebuild.build_labeled_rows_from_corrections",
        return_value=_empty_rebuild(),
    ):
        with patch(
            "lloydk.workers.tasks._create_training_run_guarded", return_value=None
        ):
            with patch(
                "lloydk.modules.m4_training.trainer.train_classifier",
                return_value=fake_report,
            ):
                with patch(
                    "lloydk.services.training_service.register_and_gate_model",
                    side_effect=RuntimeError("db down"),
                ):
                    out = train_classifier_task(spec_kwargs={})

    assert out["deploy"] == {"registered": False, "reason": "exception"}
    assert out["corrections_consumed"] == 0


def test_train_classifier_task_training_disabled_skips():
    """[재학습 토폴로지 가드 · 심층방어] enable_training=False(고객사) → 학습 미수행 조기 skip.

    가드가 무거운 trainer import 이전에 반환하므로 별도 mock 불필요 — 반환 dict만 검증한다.
    """
    with patch("lloydk.config.settings.enable_training", False, create=True):
        out = train_classifier_task(spec_kwargs={})
    assert out == {"skipped": "training_disabled"}
