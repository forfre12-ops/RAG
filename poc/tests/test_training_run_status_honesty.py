"""재학습 작업의 **상태 표기가 사실과 맞는지** 잠근다.

왜(2026-08-16). `GET /train/jobs` 가 두 가지를 잘못 말하고 있었다.

    소요시간   항상 null
               `_mark_training_run(..., "completed")` 호출부가 duration_sec 을 안 넘겨서
               `duration_sec=fields.get("duration_sec")` 가 늘 None 을 받았다.

    상태       등록이 실패해도 completed
               `register_and_gate_model` 이 예외로 죽어
               `deploy={"registered": False, "reason": "exception"}` 이 돼도
               바로 다음 줄에서 status="completed" 로 적었다. 화면에서는 새 모델이
               나온 것처럼 보이는데 실제로 등록된 모델은 없다.

**학습이 끝난 것과 모델이 등록된 것은 다른 사건이다.** 한 단어로 적으면 운영자가 구분할
방법이 없다.

⚠ `registered=False` 가 **정상**인 경우가 있다. 게이트가 자동활성을 막은 것(eval_block 등)은
  설계대로 동작한 것이지 실패가 아니다. 그래서 예외로 죽은 경우(reason="exception")만
  가른다 - 그 구분이 사라지면 정상 동작이 실패로 보고된다.

라이브 DB 없이 소스로 확인한다. 이 파일들은 Postgres 가 있어야 실행되는 경로라
단위 테스트에서 직접 돌릴 수 없다.
"""
from __future__ import annotations

import inspect


def test_completed_path_passes_duration():
    """완료 기록에 소요시간을 넘긴다 - 안 넘기면 화면이 항상 null 이다."""
    from koipa.workers import tasks

    src = inspect.getsource(tasks.retrain_from_corrections
                            if hasattr(tasks, "retrain_from_corrections") else tasks)
    assert "duration_sec=int(_time.monotonic() - _run_t0)" in src, (
        "완료 기록에 duration_sec 을 안 넘긴다 - GET /train/jobs 의 소요시간이 null 로 남는다"
    )


def test_start_time_is_monotonic():
    """시스템 시계가 바뀌어도 소요시간이 음수가 되지 않아야 한다."""
    from koipa.workers import tasks

    src = inspect.getsource(tasks)
    assert "_run_t0 = _time.monotonic()" in src, (
        "시작 시각을 monotonic 으로 안 잡는다 - 시계 변경에 흔들린다"
    )


def test_registration_crash_is_not_reported_as_completed():
    """등록이 예외로 죽으면 completed 로 적지 않는다."""
    from koipa.workers import tasks

    src = inspect.getsource(tasks)
    assert '_deploy_crashed = (_deploy.get("reason") == "exception")' in src, (
        "등록 예외 여부를 판별하지 않는다"
    )
    assert "if _deploy_crashed:" in src, "판별 결과를 상태 분기에 안 쓴다"
    # 정상 미등록(게이트 차단)까지 실패로 적으면 안 된다 - registered 플래그가 아니라
    # reason=="exception" 으로 갈라야 한다.
    assert 'registered' not in src.split('_deploy_crashed = ')[1].split('\n')[0], (
        "registered=False 로 가르면 게이트가 정상 차단한 경우까지 실패로 보고된다"
    )


def test_failed_path_keeps_what_the_run_produced():
    """실패로 적더라도 그동안 모은 것은 남긴다 - 무엇을 했는지 사라지면 안 된다."""
    from koipa.repositories.training_repo import TrainingRepo
    from koipa.workers import tasks

    params = inspect.signature(TrainingRepo.mark_failed).parameters
    assert "final_metrics" in params and "duration_sec" in params, (
        f"mark_failed 가 metrics·소요시간을 안 받는다: {list(params)}"
    )
    body = inspect.getsource(TrainingRepo.mark_failed)
    # None 이면 기존 값을 지우지 않아야 한다 - 재호출로 기록이 날아가면 안 된다
    assert "if final_metrics is not None:" in body, "None 이 기존 metrics 를 덮어쓴다"
    assert "if duration_sec is not None:" in body, "None 이 기존 소요시간을 덮어쓴다"

    helper = inspect.getsource(tasks._mark_training_run)
    assert "final_metrics=fields.get(\"final_metrics\")" in helper, (
        "실패 경로가 metrics 를 repo 로 안 넘긴다"
    )
