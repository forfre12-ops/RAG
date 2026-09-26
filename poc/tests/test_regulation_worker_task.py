"""규정 색인 태스크 배선 — 이름·큐·시간 제한·재시도, 그리고 **워커가 그 큐를 실제로 소비하는가**.

왜 이 시험이 있는가. 색인이 큐로 나가는 순간부터 화면은 「규정을 분석하는 중…」을 보인다. 워커가 그 큐를 안 듣거나
태스크가 조용히 죽으면 판이 **영원히 분석 중**으로 남는다 — 오류도 없고 화면은 진행 중이라고 말한다. 서비스 시험
(test_regulation_service_db.py)은 큐를 건너뛰고 색인 함수를 직접 부르므로 이 배선을 못 본다.

브로커·워커 없이 돈다 — 태스크 함수와 설정만 읽는다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from celery.exceptions import Retry

import koipa.workers.tasks as T
from koipa.workers.celery_app import celery_app

POC = Path(__file__).resolve().parents[1]
TASK = "koipa.index_regulation"


# ── 등록·라우팅·시간 제한 ─────────────────────────────────────────────────────

def test_task_is_registered_under_its_celery_name():
    assert T.index_regulation.name == TASK
    assert TASK in celery_app.tasks


def test_task_goes_to_the_index_queue():
    """새 큐 이름을 만들지 않는다 — 워커 기동 명령 넷(compose 셋·Dockerfile.worker)을 고쳐야 하기 때문이다."""
    assert celery_app.conf.task_routes[TASK] == {"queue": "index"}


def test_time_limits_are_long_enough_for_a_large_regulation_and_soft_is_below_hard():
    """전역 한도(soft 900·hard 1200초)로는 큰 규정 색인이 잘린다. 설계 상한(문장 3,000)의 실측 외삽은 약 14분이다."""
    ann = celery_app.conf.task_annotations[TASK]
    assert ann["soft_time_limit"] >= 3300 and ann["time_limit"] >= 3600
    assert ann["soft_time_limit"] < ann["time_limit"]                 # 전역 validator 와 같은 규칙: soft < hard


def test_retries_are_bounded_like_the_other_index_tasks():
    assert T.index_regulation.max_retries == 2
    assert T.index_regulation.max_retries == T.index_document_vector.max_retries


def _worker_queue_lists() -> list[tuple[str, list[str]]]:
    """리포에서 Celery 워커를 띄우는 모든 명령의 (출처, 소비 큐 목록). `-Q` 가 없으면 빈 목록 — 기본 큐만 듣는다."""
    out: list[tuple[str, list[str]]] = []
    files = [*POC.glob("docker-compose*.yml"), POC / "Dockerfile.worker", POC / "Makefile"]
    for f in files:
        if not f.exists():
            continue
        for ln in f.read_text(encoding="utf-8").splitlines():
            if not re.search(r"celery.{0,40}koipa\.workers\.celery_app.{0,40}\bworker\b", ln):
                continue
            m = re.search(r"""-Q["',\s]+([A-Za-z0-9_,]+)""", ln)
            out.append((f"{f.name}: {ln.strip()[:100]}", m.group(1).split(",") if m else []))
    return out


def test_every_worker_start_command_consumes_the_index_queue():
    """워커가 하나라도 `index` 큐를 안 들으면 규정 색인(과 문서 벡터 색인)이 그 배포에서 영원히 대기한다."""
    found = _worker_queue_lists()
    assert len(found) >= 4, f"워커 기동 명령을 제대로 못 찾았다({len(found)}건): {found}"
    missing = [src for src, queues in found if "index" not in queues]
    assert not missing, "index 큐를 소비하지 않는 워커 기동 명령:\n  " + "\n  ".join(missing)


# ── 태스크 동작(재시도·마지막 재시도 실패의 상태 기록) ─────────────────────────

class _Svc:
    def __init__(self, result=None, error: Exception | None = None):
        self.result, self.error = result, error
        self.marked: list[tuple[str, str]] = []
        self.indexed: list[str] = []

    def index(self, reg_id):
        self.indexed.append(reg_id)
        if self.error:
            raise self.error
        return self.result

    def mark_failed(self, reg_id, message):
        self.marked.append((reg_id, message))


@pytest.fixture
def fake_service(monkeypatch):
    from koipa.services.regulation_service import RegulationService

    holder = {}

    def install(svc):
        holder["svc"] = svc
        monkeypatch.setattr(RegulationService, "get_instance", classmethod(lambda cls: svc))
        return svc

    return install


def _run_with_retries(retries: int, reg_id: str = "r-1"):
    """워커 안에서 `retries` 번째 시도인 것처럼 태스크 본문을 부른다(브로커 없이 — retry 는 예외로 대체)."""
    calls = {"retry": []}

    def fake_retry(exc=None, countdown=None, **kw):
        calls["retry"].append(countdown)
        raise Retry(exc=exc, when=countdown)

    T.index_regulation.push_request(retries=retries, called_directly=False)
    orig = T.index_regulation.retry
    T.index_regulation.retry = fake_retry
    try:
        try:
            return T.index_regulation.run(reg_id), calls, None
        except Retry as exc:
            return None, calls, exc
    finally:
        T.index_regulation.retry = orig
        T.index_regulation.pop_request()


def test_success_returns_the_service_result(fake_service):
    svc = fake_service(_Svc(result={"status": "ready", "reg_id": "r-1"}))
    res, calls, retry = _run_with_retries(0)
    assert res == {"status": "ready", "reg_id": "r-1"} and svc.indexed == ["r-1"]
    assert retry is None and calls["retry"] == [] and svc.marked == []


def test_transient_failure_is_retried_with_growing_countdown_and_does_not_mark_failed(fake_service):
    svc = fake_service(_Svc(error=RuntimeError("모델을 불러오지 못했다")))
    _, calls0, retry0 = _run_with_retries(0)
    _, calls1, retry1 = _run_with_retries(1)
    assert isinstance(retry0, Retry) and isinstance(retry1, Retry)
    assert calls0["retry"] == [10] and calls1["retry"] == [20]          # 10초 · 20초
    assert svc.marked == []                                              # 아직 포기하지 않았다 — 판은 분석 중으로 남는다


def test_last_retry_failure_leaves_the_regulation_failed_with_a_reason_instead_of_hanging(fake_service):
    """마지막 재시도까지 실패하면 판을 실패로 남긴다 — 안 그러면 화면이 영원히 「분석 중」이라고 말한다."""
    svc = fake_service(_Svc(error=RuntimeError("디스크가 가득 찼다")))
    res, calls, retry = _run_with_retries(T.index_regulation.max_retries)
    assert retry is None and calls["retry"] == []                        # 더 재시도하지 않는다
    assert res["status"] == "failed" and "디스크가 가득 찼다" in res["error"]
    assert len(svc.marked) == 1 and svc.marked[0][0] == "r-1"
    assert "색인에 실패했습니다" in svc.marked[0][1] and "디스크가 가득 찼다" in svc.marked[0][1]
