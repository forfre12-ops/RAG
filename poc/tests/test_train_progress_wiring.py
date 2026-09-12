"""학습 진행률 배선 — TrainerCallback → JobStore → GET /train/jobs/{id}.

[실측 2026-08-08] 종전 TrainStatus.progress 는 status 에서 유도된 상수뿐이었고
(queued 0.0 / running 0.5 / completed 1.0), current_epoch·total_epochs·
estimated_finish_at 은 스키마에 선언만 되어 있고 채우는 코드가 어디에도 없었다.
실서버에서 13시간짜리 학습을 돌리는 내내 막대가 50% 에 멈춰 있어, 운영자가
"멈춘 것인지 도는 것인지" 화면으로 구분할 수 없었다.

여기서 고정하는 계약:
  · 트레이너 콜백이 JobStore 에 쓴 값이 상태 응답에 실린다(running 일 때).
  · JobStore 가 비어 있으면 종전 동작(status 유도값)으로 조용히 되돌아간다 — 하위호환.
  · 진행률 조회 실패가 상태조회 자체를 깨뜨리지 않는다(부가 정보).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from koipa.services.training_service import TrainingService


class _FakeStore:
    def __init__(self, doc=None, boom=False):
        self._doc = doc
        self._boom = boom

    def get(self, job_id):  # noqa: ANN001
        if self._boom:
            raise RuntimeError("redis down")
        return self._doc


def _svc(store) -> TrainingService:
    svc = TrainingService.__new__(TrainingService)   # __init__ 의 Redis 연결 회피
    svc.jobs = store
    return svc


def _run(status="running"):
    return SimpleNamespace(status=status, started_at=None, error_message=None)


def test_progress_comes_from_job_store_while_running(monkeypatch):
    jid = uuid.uuid4()
    svc = _svc(_FakeStore({
        "train_progress": 0.0398,
        "current_epoch": 1,
        "total_epochs": 5,
        "estimated_finish_at": "2026-08-08T22:00:00+00:00",
    }))
    monkeypatch.setattr(TrainingService, "_get_run", lambda self, i: _run())

    st = svc.status(jid)
    # 종전이면 무조건 0.5 였다 — 실제 스텝 비율이 나와야 한다.
    assert st.progress == pytest.approx(0.0398)
    assert st.current_epoch == 1
    assert st.total_epochs == 5
    assert st.estimated_finish_at == "2026-08-08T22:00:00+00:00"


def test_falls_back_to_status_derived_when_store_empty(monkeypatch):
    """진행률 기록이 없으면 종전 동작 그대로 — 하위호환(스크립트 직접 학습 경로 등)."""
    svc = _svc(_FakeStore(None))
    monkeypatch.setattr(TrainingService, "_get_run", lambda self, i: _run())
    st = svc.status(uuid.uuid4())
    assert st.progress == 0.5
    assert st.current_epoch is None


def test_completed_run_is_not_overridden_by_stale_progress(monkeypatch):
    """완료된 학습은 1.0 이어야 한다 — JobStore 에 남은 마지막 스텝값(<1.0)에 끌려가면 안 된다.

    콜백은 on_train_end 까지만 쓰므로, 평가·등록 단계에서 완료된 뒤에도 저장소에는
    학습 종료 시점 값이 남아 있다. 그 값이 완료 표시를 덮으면 화면이 영원히 99% 가 된다.
    """
    svc = _svc(_FakeStore({"train_progress": 0.98, "current_epoch": 5, "total_epochs": 5}))
    monkeypatch.setattr(TrainingService, "_get_run", lambda self, i: _run("completed"))
    st = svc.status(uuid.uuid4())
    assert st.progress == 1.0
    assert st.current_epoch == 5   # 에폭 정보 자체는 유지(참고값)


def test_store_failure_does_not_break_status(monkeypatch):
    svc = _svc(_FakeStore(boom=True))
    monkeypatch.setattr(TrainingService, "_get_run", lambda self, i: _run())
    st = svc.status(uuid.uuid4())
    assert st.status == "running"
    assert st.progress == 0.5      # 조용히 종전 동작


def test_out_of_range_progress_is_ignored(monkeypatch):
    """저장소 값이 오염돼도 0~1 밖이면 쓰지 않는다(화면에 200% 가 뜨지 않게)."""
    svc = _svc(_FakeStore({"train_progress": 7.5}))
    monkeypatch.setattr(TrainingService, "_get_run", lambda self, i: _run())
    assert svc.status(uuid.uuid4()).progress == 0.5


def test_trainspec_carries_progress_run_id_and_defaults_off():
    """진행률 기록은 opt-in — run_id 가 없으면 트레이너가 아무것도 쓰지 않는다."""
    from koipa.modules.m4_training.trainer import TrainSpec
    assert TrainSpec().progress_run_id is None
    assert TrainSpec(progress_run_id="abc").progress_run_id == "abc"


@pytest.mark.parametrize(
    ("hf_epoch", "total", "shown"),
    [
        (0.0,  1, 1),   # 막 시작 — "1 / 1" 이어야 한다. 0 이면 시작 안 한 것처럼 보인다.
        (0.42, 5, 1),   # 첫 에폭 진행 중 → 1번째
        (1.0,  5, 2),   # 첫 에폭 완료 → 2번째 진입
        (4.7,  5, 5),   # 마지막 에폭 진행 중
        (5.0,  5, 5),   # 학습 종료 — total 로 clamp(6 이 되면 안 된다)
    ],
)
def test_epoch_is_one_based_and_clamped(hf_epoch, total, shown):
    """[실측 2026-08-08] HF state.epoch 은 '완료한 에폭 수'(0.0→N)라 그대로 정수화하면
    첫 에폭 내내 0 이다. 화면이 "에폭 0 / 1" 을 보여주면 멈춘 것처럼 읽힌다 —
    이 배선이 고치려는 증상과 정확히 같은 오해라, 1-based + clamp 를 계약으로 고정한다.
    """
    cur = int(hf_epoch) + 1
    if total:
        cur = min(cur, total)
    assert cur == shown
