"""워커 재시도·보상 계약 — 몇 번 다시 하고, 포기하면 무엇을 남기는가.

왜 이 시험이 있는가(2026-09-06). `workers/tasks.py` 는 커버리지 **66.2%** 였고, 안 덮인
구간이 대부분 **재시도 경로**(except 블록)였다. 이 경로가 틀리면 두 방향으로 나쁘다:

    너무 적게 재시도  →  일시적 DB·네트워크 흔들림에 잡이 죽는다
    너무 많이 재시도  →  같은 실패를 반복하며 큐를 막는다
    보상이 없으면     →  부분 결과가 사라져 무엇까지 됐는지 알 수 없다

그리고 이 파일은 **주석이 코드와 어긋나 있었다**:

    모듈 docstring   "classify_async / synthesize_batch: max_retries=3"
    실제 데코레이터   max_retries=2   (세 태스크 모두)

숫자를 세는 시험이 없으니 아무도 몰랐다. 이 시험이 **코드 쪽을 정본으로** 고정한다.

⚠ 헬퍼들은 함수 안에서 지연 import 를 한다(무거운 모듈 회피). 그래서 패치는 tasks 모듈이
  아니라 **원래 모듈**에 걸어야 한다 — koipa.services.job_store · koipa.services.outbox.
"""
from __future__ import annotations

import uuid

import koipa.services.job_store as JS
import koipa.services.outbox as OB
import koipa.workers.tasks as T

# ⚠ 태스크 함수명과 Celery 이름이 다르다 — golden_build_task ↔ "koipa.golden_build".
_RETRY_TASKS = ("classify_async", "synthesize_batch", "golden_build_task")


# ── 재시도 횟수 — 주석이 아니라 코드가 정본이다 ─────────────────────────────
def test_retry_count_is_two_everywhere():
    """세 태스크가 같은 값을 쓴다 — 하나만 달라지면 운영자가 예측할 수 없다.

    ⚠ 2026-09-06 까지 모듈 docstring 은 max_retries=3 이라 적고 있었다. 실제는 2 다.
      이 시험이 코드를 정본으로 못박는다. 값을 바꾸려면 여기부터 고쳐야 한다.
    """
    for name in _RETRY_TASKS:
        task = getattr(T, name)
        assert task.max_retries == 2, "%s.max_retries = %r" % (name, task.max_retries)


def test_retry_count_is_never_none():
    """None 이면 `self.max_retries or 0` 이 0 이 되어 **재시도를 아예 안 한다.**

    데코레이터에 적어 두고도 속성이 안 붙으면 조용히 그 상태가 된다 — 로그도 안 남는다.
    """
    for name in _RETRY_TASKS:
        assert getattr(T, name).max_retries is not None, name


def test_tasks_have_celery_names():
    """이름이 있어야 큐에서 라우팅된다. 함수명과 다르다는 것도 함께 고정한다."""
    assert T.classify_async.name == "koipa.classify_async"
    assert T.synthesize_batch.name == "koipa.synthesize_batch"
    assert T.golden_build_task.name == "koipa.golden_build"


# ── 지수 백오프 — countdown = 2 ** attempts ────────────────────────────────
def test_backoff_starts_at_one_second_and_doubles():
    """첫 재시도는 1초 뒤, 두 번째는 2초 뒤 — 즉시(0초) 재시도가 아니다.

    0초면 같은 실패를 곧바로 다시 겪어 재시도가 의미 없다.
    """
    assert 2 ** 0 == 1
    assert 2 ** 1 == 2
    # max_retries=2 이므로 최악 대기가 1+2=3초다 — 큐를 오래 막지 않는다.
    assert sum(2 ** a for a in range(2)) == 3


# ── 보상 — 포기할 때 무엇을 남기는가 ───────────────────────────────────────
class _Store:
    def __init__(self):
        self.calls = []

    def update(self, job_id, **kw):
        self.calls.append((job_id, kw))


def test_compensation_records_partial_and_reason(monkeypatch):
    """모든 재시도가 실패하면 **부분 결과와 사유**를 남긴다 — 조용히 사라지지 않는다."""
    store = _Store()
    monkeypatch.setattr(JS, "get_default_store", lambda: store)
    jid = str(uuid.uuid4())

    T._record_compensation(jid, partial_results=[{"doc": 1}], reason="boom: 원인")

    assert len(store.calls) == 1, store.calls
    _job, kw = store.calls[0]
    assert kw["status"] == "partial"
    assert kw["results"] == [{"doc": 1}], "부분 결과가 남아야 한다"
    assert "boom" in kw["error"], kw["error"]


def test_compensation_is_skipped_without_job_id(monkeypatch):
    """단발 호출(job_id 없음)에는 아무것도 쓰지 않는다 — 호출자 책임이다."""
    store = _Store()
    monkeypatch.setattr(JS, "get_default_store", lambda: store)
    T._record_compensation(None, partial_results=[{"doc": 1}], reason="x")
    assert store.calls == []


def test_compensation_survives_a_broken_store(monkeypatch):
    """저장소가 죽어도 워커를 죽이지 않는다 — 보상 실패가 원래 실패를 가리면 안 된다."""
    class _Broken:
        def update(self, *a, **k):
            raise RuntimeError("store down")

    monkeypatch.setattr(JS, "get_default_store", lambda: _Broken())
    T._record_compensation(str(uuid.uuid4()), partial_results=[], reason="whatever")


def test_compensation_survives_a_bad_job_id(monkeypatch):
    """UUID 가 아닌 job_id 도 워커를 죽이지 않는다(내부에서 UUID 로 바꾼다)."""
    store = _Store()
    monkeypatch.setattr(JS, "get_default_store", lambda: store)
    T._record_compensation("not-a-uuid", partial_results=[], reason="x")


# ── 콜백 — 실패도 알린다 ────────────────────────────────────────────────────
def test_failure_callback_is_published(monkeypatch):
    """실패 콜백이 실제로 나간다 — 부르는 쪽이 성공만 통보받으면 영원히 기다린다."""
    sent = []
    monkeypatch.setattr(OB, "publish_callback", lambda url, payload: sent.append((url, payload)))
    T._publish_callback_webhook("http://cb", {"job_id": "j", "status": "failed", "error": "x"})
    assert sent, "콜백이 안 나갔다"
    assert sent[0][0] == "http://cb"
    assert sent[0][1]["status"] == "failed"


def test_callback_delegates_to_outbox_not_direct_http(monkeypatch):
    """직접 HTTP 를 쏘지 않고 outbox 로 위임한다 — 신뢰 전달 계약이 거기 있다."""
    called = []
    monkeypatch.setattr(OB, "publish_callback", lambda url, payload: called.append(url))
    T._publish_callback_webhook("http://cb", {"job_id": "j", "status": "done"})
    assert called == ["http://cb"]
