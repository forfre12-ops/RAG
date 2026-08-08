"""고객사 야간 무인 재학습 스케줄 — 기본 미등록(수동 트리거), 설정으로만 복원.

[실측 2026-08-08] 매일 02:00 KST 무인 발화가 걸려 있었는데 실측이 그 전제를 부정했다:
  · 이 틱이 태우는 것은 train_classifier_task(spec_kwargs=None) = 기본 TrainSpec
    (5에폭 풀 파인튜닝). CPU 실측 약 13시간 → 02:00 시작이면 15:00 종료로, 야간에 끝나지
    않고 고객사 업무시간 CPU 를 점유한다.
  · 고객사 프로파일 워커(4GiB)에서 같은 태스크를 실제로 발화하니 40초 만에 OOMKilled
    (2.805GiB → kill). enable_training=False 여도 enable_incremental_retrain 이 학습 가드를
    열어 주므로 막히지 않았다.
  · 새벽에 죽어도 화면에 신호가 없다.

여기서 고정하는 계약:
  1. 기본값에서 beat 에 등록되지 않는다(무인 발화 없음).
  2. 다른 주기 작업은 영향받지 않는다.
  3. 설정을 켜면 02:00 스케줄이 그대로 복원된다(기능 삭제가 아니라 기본값 변경).
  4. 태스크 자체는 남아 있어 수동 호출이 가능하다(반출 없이 현장 반영이라는 원래 목적 유지).
"""
from __future__ import annotations

import importlib

import pytest

_KEY = "nightly-incremental-retrain-0200"
_TASK = "lloydk.nightly_incremental_retrain_tick"


def _reload_celery(monkeypatch, *, enabled: bool):
    """설정을 바꾼 뒤 celery_app 모듈을 다시 읽어 beat_schedule 을 재구성한다.

    beat_schedule 은 import 시점에 만들어지므로, 설정 효과를 보려면 재로드가 필요하다.
    """
    from lloydk import config as config_mod
    monkeypatch.setattr(config_mod.settings, "enable_nightly_retrain_schedule", enabled)
    import lloydk.workers.celery_app as mod
    return importlib.reload(mod)


def test_nightly_schedule_not_registered_by_default(monkeypatch):
    mod = _reload_celery(monkeypatch, enabled=False)
    assert _KEY not in mod.celery_app.conf.beat_schedule


def test_other_periodic_jobs_unaffected(monkeypatch):
    """야간 재학습만 빠지고 나머지 주기 작업(감사체인·파티션·outbox 등)은 그대로여야 한다."""
    mod = _reload_celery(monkeypatch, enabled=False)
    sched = mod.celery_app.conf.beat_schedule
    assert len(sched) >= 5
    tasks = {v.get("task") for v in sched.values()}
    assert "lloydk.verify_audit_chain_tick" in tasks
    assert "lloydk.deliver_outbox_tick" in tasks
    assert _TASK not in tasks


def test_setting_restores_schedule(monkeypatch):
    """기능 삭제가 아니라 기본값 변경 — 사양이 되는 회원사는 설정만 켜면 종전 동작."""
    mod = _reload_celery(monkeypatch, enabled=True)
    entry = mod.celery_app.conf.beat_schedule.get(_KEY)
    assert entry is not None, "설정을 켜면 02:00 스케줄이 복원돼야 한다"
    assert entry["task"] == _TASK
    # crontab(minute=0, hour=2) — 시각이 바뀌지 않았는지까지 고정
    assert set(entry["schedule"].hour) == {2}
    assert set(entry["schedule"].minute) == {0}


def test_task_still_callable_for_manual_trigger(monkeypatch):
    """스케줄을 껐다고 태스크가 사라지면 안 된다 — 수동 트리거 경로가 남아야 한다."""
    _reload_celery(monkeypatch, enabled=False)
    from lloydk.workers.tasks import nightly_incremental_retrain_tick
    assert callable(nightly_incremental_retrain_tick)


@pytest.fixture(autouse=True)
def _restore_module():
    """테스트가 모듈을 재로드하므로 끝나고 기본 상태로 되돌린다(다른 테스트 오염 방지)."""
    yield
    import lloydk.workers.celery_app as mod
    importlib.reload(mod)
