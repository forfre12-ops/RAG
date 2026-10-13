"""배치(/classify/batch)의 Celery 비동기 발사 + 과대 문서 가드 (2026-09-30).

이전에는 submit_async와 달리 submit_batch에 Celery 발사 분기가 아예 없어, 브로커가
떠 있어도 최대 1000건을 요청 안에서 전부 순차 처리했다 — 문서가 작아도 수 분, 큰 문서가
섞이면 수십 분까지 걸려 호출자 연결이 응답 전에 끊기고, job_id는 그 응답 안에만 있어
끊기면 폴링할 방법도 없었다. 이 파일은 (1) 발사 분기 자체, (2) 발사 실패 시 in-process
폴백, (3) classify_batch task가 실제로 in-process 경로와 같은 결과를 내는지, (4) 문서
한 건이 너무 크면 재시도 없이 즉시 실패 처리되는지를 잰다.

패턴은 test_dispatch_probe_cache.py::test_enqueue_failure_drops_saved_result 를 따른다
(submit_async의 이미 검증된 Celery-분기 시험 방식을 submit_batch에도 적용).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.slow

import koipa.services.async_classify_service as acs
from koipa.schemas.classify import ClassifyRequest
from koipa.schemas.classify_async import ClassifyBatchRequest
from koipa.services.async_classify_service import AsyncClassifyService


@pytest.fixture(autouse=True)
def _reset_singleton():
    yield
    from koipa.services.classify_service import ClassifyService
    ClassifyService._instance = None


def _docs(n: int) -> ClassifyBatchRequest:
    return ClassifyBatchRequest(
        documents=[ClassifyRequest(doc_id=f"cd{i}", content=f"sample content {i}") for i in range(n)]
    )


# ----------------------------------------------------------------------
# Celery 발사 분기
# ----------------------------------------------------------------------

def test_batch_dispatches_to_celery_when_available(monkeypatch):
    """브로커 가용이면 즉시 job_id+status=queued를 반환하고, 처리는 워커에 위임한다."""
    import koipa.workers.tasks as tasks

    dispatched = {}

    class _Fake:
        @staticmethod
        def delay(payload, *, job_id=None, callback_url=None):
            dispatched["payload"] = payload
            dispatched["job_id"] = job_id
            dispatched["callback_url"] = callback_url

    monkeypatch.setattr(acs, "_celery_dispatch_available", lambda: True)
    monkeypatch.setattr(tasks, "classify_batch", _Fake)

    svc = AsyncClassifyService(sleep_fn=lambda _s: None)
    req = _docs(3)
    res = svc.submit_batch(req)

    assert res.status == "queued"
    assert res.total == 3
    assert res.completed == 0
    assert res.failed == 0
    assert res.failed_doc_ids == []
    assert dispatched["job_id"] == str(res.job_id)
    assert len(dispatched["payload"]) == 3
    assert dispatched["payload"][0]["doc_id"] == "cd0"


def test_batch_dispatch_failure_falls_back_to_in_process(monkeypatch):
    """발사 자체가 실패(예: 접속 끊김)하면 in-process 로 폴백해 끝까지 처리한다."""
    import koipa.workers.tasks as tasks

    class _Broken:
        @staticmethod
        def delay(*_a, **_k):
            raise ConnectionError("broker went away")

    dropped = {"n": 0}
    monkeypatch.setattr(acs, "_celery_dispatch_available", lambda: True)
    monkeypatch.setattr(
        acs, "invalidate_dispatch_cache", lambda: dropped.__setitem__("n", dropped["n"] + 1)
    )
    monkeypatch.setattr(tasks, "classify_batch", _Broken)

    svc = AsyncClassifyService(sleep_fn=lambda _s: None)
    res = svc.submit_batch(_docs(3))

    assert res.status in ("done", "partial", "failed"), "폴백은 최종 상태로 끝나야 한다(queued 아님)"
    assert res.completed + res.failed == 3
    assert dropped["n"] == 1


def test_batch_stays_in_process_when_celery_unavailable():
    """브로커 미가용(시험 기본값)이면 지금까지와 같은 in-process 동작을 유지한다."""
    svc = AsyncClassifyService(sleep_fn=lambda _s: None)
    res = svc.submit_batch(_docs(3))
    assert res.status == "done"
    assert res.completed == 3


# ----------------------------------------------------------------------
# classify_batch task 자체 — in-process 경로와 같은 두 메서드를 공유하므로 같은 결과를 내야 함
# ----------------------------------------------------------------------

def test_classify_batch_task_matches_in_process_result():
    import uuid as _uuid

    from koipa.workers.tasks import classify_batch

    svc = AsyncClassifyService(sleep_fn=lambda _s: None)
    job_id = _uuid.uuid4()
    svc.jobs.create(job_id, payload={"total": 2, "completed": 0})
    payload = [
        {"doc_id": "task1", "content": "본 문서는 일반 업무 자료입니다"},
        {"doc_id": "task2", "content": "본 문서는 영업비밀 관련 자료입니다"},
    ]

    out = classify_batch.run(payload, job_id=str(job_id), callback_url=None)

    assert out["total"] == 2
    assert out["completed"] == 2
    assert out["status"] == "done"

    status = svc.get_status(job_id)
    assert status is not None
    assert status.status == "done"
    assert status.completed == 2


# ----------------------------------------------------------------------
# 과대 문서 가드 — 재시도 없이 즉시 실패, 다른 문서는 계속 처리
# ----------------------------------------------------------------------

def test_oversized_document_fails_immediately_without_blocking_others(monkeypatch):
    from koipa.config import settings

    monkeypatch.setattr(settings, "analyze_sync_max_chunks", 2, raising=False)
    monkeypatch.setattr(settings, "chunk_overlap", 0, raising=False)

    huge = "본 문서는 대용량 테스트용 반복 문단입니다. " * 3000  # 넉넉히 2청크 초과
    req = ClassifyBatchRequest(
        documents=[
            ClassifyRequest(doc_id="small1", content="짧은 문서입니다"),
            ClassifyRequest(doc_id="huge1", content=huge),
            ClassifyRequest(doc_id="small2", content="또 다른 짧은 문서"),
        ]
    )
    svc = AsyncClassifyService(sleep_fn=lambda _s: None)
    res = svc.submit_batch(req)

    assert res.total == 3
    assert "huge1" in res.failed_doc_ids
    assert res.completed == 2, "작은 문서 2건은 큰 문서와 무관하게 처리돼야 한다"
    huge_err = next(e for e in res.errors if e["doc_id"] == "huge1")
    assert huge_err["error_type"] == "DocumentTooLarge"
    assert huge_err["attempts"] == 0, "크기 문제는 재시도해도 안 줄어드니 재시도하지 않아야 한다"


def test_oversized_guard_off_when_content_missing():
    """content가 없는 요청(다른 경로로 본문을 채우는 경우)은 크기를 알 수 없어 통과시킨다."""
    from koipa.services.async_classify_service import _oversized_batch_doc_reason

    doc = ClassifyRequest(doc_id="no-content")
    assert _oversized_batch_doc_reason(doc) is None
