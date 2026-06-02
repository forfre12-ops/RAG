"""AsyncClassifyService 기본 동작 검증.

대상: lloydk.services.async_classify_service.AsyncClassifyService
- submit_async 단일건 처리 + JobStore에 done 상태 기록
- submit_async 실패 시 status=failed + error 메시지 기록
- submit_batch 정상 경로 (총량·상태 url 형식·get_status round-trip)
- _strip_async_fields 가 callback_url을 제거

부분 실패·retry 시나리오는 test_batch_retry.py에 분리됨.
"""

from __future__ import annotations

import pytest
pytestmark = pytest.mark.slow

from lloydk.schemas.classify import ClassifyRequest
from lloydk.schemas.classify_async import ClassifyAsyncRequest, ClassifyBatchRequest
from lloydk.services.async_classify_service import AsyncClassifyService


@pytest.fixture(autouse=True)
def _reset_singleton():
    yield
    from lloydk.services.classify_service import ClassifyService
    ClassifyService._instance = None


def _async_req(doc_id: str = "a-1", content: str = "샘플 내용 영업비밀") -> ClassifyAsyncRequest:
    return ClassifyAsyncRequest(doc_id=doc_id, content=content)


def _batch_req(n: int) -> ClassifyBatchRequest:
    return ClassifyBatchRequest(
        documents=[ClassifyRequest(doc_id=f"b{i}", content=f"내용 {i}") for i in range(n)]
    )


def test_submit_async_happy_path():
    svc = AsyncClassifyService(sleep_fn=lambda _s: None)
    res = svc.submit_async(_async_req("a-1"))
    assert res.status == "queued"
    assert str(res.job_id) in res.status_url

    status = svc.get_status(res.job_id)
    assert status is not None
    assert status.status == "done"
    assert status.completed == 1
    assert status.results is not None and len(status.results) == 1
    assert status.results[0].doc_id == "a-1"


def test_submit_async_records_failure():
    svc = AsyncClassifyService(sleep_fn=lambda _s: None)

    def _boom(_req):
        raise RuntimeError("inference broke")

    svc.classify.classify = _boom  # type: ignore[attr-defined]

    res = svc.submit_async(_async_req("a-err"))
    status = svc.get_status(res.job_id)
    assert status is not None
    assert status.status == "failed"
    assert status.error is not None
    assert "inference broke" in status.error


def test_submit_batch_happy_path_3_docs():
    svc = AsyncClassifyService(sleep_fn=lambda _s: None)
    res = svc.submit_batch(_batch_req(3))
    assert res.total == 3
    assert res.completed == 3
    assert res.failed == 0
    assert res.failed_doc_ids == []
    assert res.errors == []

    status = svc.get_status(res.job_id)
    assert status is not None
    assert status.status == "done"
    assert status.completed == 3
    assert status.results is not None and len(status.results) == 3


def test_get_status_returns_none_for_unknown_job_id():
    import uuid

    svc = AsyncClassifyService(sleep_fn=lambda _s: None)
    assert svc.get_status(uuid.uuid4()) is None


def test_strip_async_fields_removes_callback_url():
    req = ClassifyAsyncRequest(
        doc_id="a-2", content="x", callback_url="https://example.com/cb",
    )
    stripped = AsyncClassifyService._strip_async_fields(req)
    assert isinstance(stripped, ClassifyRequest)
    assert not hasattr(stripped, "callback_url") or getattr(stripped, "callback_url", None) is None
    assert stripped.doc_id == "a-2"
