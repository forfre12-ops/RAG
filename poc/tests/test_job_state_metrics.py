"""[obs] 작업 상태 진입 메트릭 — job_store create/update choke point 배선.

CELERY_QUEUE_LENGTH(큐 길이)만 노출되던 사각지대 해소: 상태 전이 1회당 1 inc(double-count
없음), 터미널 상태는 완료 소요시간 관측. _created_at 주입(블로커 수정)으로 duration 동작.
값은 자식 카운터 내부 _value로 직접 읽어 sample 이름 규칙에 비의존, before/after 델타로 검증.
"""

from __future__ import annotations

import uuid

from koipa.api import prom_metrics as pm
from koipa.services.job_store import InMemoryJobStore, _emit_job_state_metric


def _state_val(state) -> float:
    return pm.JOB_STATE_ENTERED_TOTAL.labels(state=state)._value.get()


def _dur_count(state) -> float:
    # Histogram의 _count 표본(observe 호출 횟수) — registry exposition으로 조회.
    v = pm.registry.get_sample_value(
        "koipa_job_completion_duration_seconds_count", {"state": state}
    )
    return 0.0 if v is None else v


def test_create_injects_created_at_and_counts_queued():
    store = InMemoryJobStore()
    jid = uuid.uuid4()
    before = _state_val("queued")
    store.create(jid, {"total": 3})
    job = store.get(jid)
    assert job is not None and "_created_at" in job  # 블로커 수정 검증
    assert _state_val("queued") == before + 1


def test_terminal_transition_counts_state_and_observes_duration():
    store = InMemoryJobStore()
    jid = uuid.uuid4()
    store.create(jid, {})
    s0 = _state_val("done")
    d0 = _dur_count("done")
    store.update(jid, status="done")
    assert _state_val("done") == s0 + 1
    assert _dur_count("done") == d0 + 1  # _created_at 있으니 duration 관측됨


def test_failed_and_partial_states_counted():
    store = InMemoryJobStore()
    for st in ("failed", "partial"):
        jid = uuid.uuid4()
        store.create(jid, {})
        before = _state_val(st)
        store.update(jid, status=st)
        assert _state_val(st) == before + 1


def test_non_status_update_does_not_count():
    store = InMemoryJobStore()
    jid = uuid.uuid4()
    store.create(jid, {})
    before_done = _state_val("done")
    store.update(jid, progress=0.5)  # status 없음 → 상태 카운터 무변동
    assert _state_val("done") == before_done


def test_update_missing_job_does_not_count():
    store = InMemoryJobStore()
    before = _state_val("done")
    store.update(uuid.uuid4(), status="done")  # 미존재 job → 조기 반환, 카운트 안 됨
    assert _state_val("done") == before


def test_emit_helper_best_effort_on_bad_created_at():
    # created_at이 None/비정상이어도 예외 전파 없음(분류·작업 경로 무영향).
    _emit_job_state_metric("done", None)
    _emit_job_state_metric("done", "not-a-number")
    _emit_job_state_metric("queued")
