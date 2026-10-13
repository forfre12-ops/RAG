"""AsyncClassifyService 기본 동작 검증.

대상: koipa.services.async_classify_service.AsyncClassifyService
- submit_async 단일건 처리 + JobStore에 done 상태 기록
- submit_async 실패 시 status=failed + error 메시지 기록
- submit_batch 정상 경로 (총량·상태 url 형식·get_status round-trip)
- _strip_async_fields 가 callback_url을 제거

부분 실패·retry 시나리오는 test_batch_retry.py에 분리됨.
"""

from __future__ import annotations

import pytest
pytestmark = pytest.mark.slow

from koipa.schemas.classify import ClassifyRequest
from koipa.schemas.classify_async import ClassifyAsyncRequest, ClassifyBatchRequest
from koipa.services.async_classify_service import AsyncClassifyService


@pytest.fixture(autouse=True)
def _reset_singleton():
    yield
    from koipa.services.classify_service import ClassifyService
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
    # Top-7 배선: 테스트/브로커 미가용은 in-process 즉시 처리이므로 응답 status가
    # 'queued' 거짓표기가 아니라 실제 최종 상태('done')를 반영한다.
    assert res.status == "done"
    assert str(res.job_id) in res.status_url

    status = svc.get_status(res.job_id)
    assert status is not None
    assert status.status == "done"
    assert status.completed == 1
    assert status.results is not None and len(status.results) == 1
    assert status.results[0].doc_id == "a-1"


def test_submit_async_preserves_client_request_id_in_result():
    """[2026-10-13 결함 수정] 단건 경로는 job 레벨(ClassifyJobStatus.client_request_id)에는
    예전에도 값이 있었지만, 결과 건(results[0])에는 _strip_async_fields 가 떼어내 못 실렸다."""
    svc = AsyncClassifyService(sleep_fn=lambda _s: None)
    req = ClassifyAsyncRequest(doc_id="a-crid", content="샘플 내용", client_request_id="req-single")
    res = svc.submit_async(req)
    assert res.status == "done"

    status = svc.get_status(res.job_id)
    assert status is not None
    # job 레벨(기존 배선)과 결과 건 레벨(이번 수정) 둘 다 맞아야 한다.
    assert status.client_request_id == "req-single"
    assert status.results is not None and len(status.results) == 1
    assert status.results[0].client_request_id == "req-single"


def test_submit_async_records_failure():
    svc = AsyncClassifyService(sleep_fn=lambda _s: None)

    def _boom(_req):
        raise RuntimeError("inference broke")

    svc.classify.classify = _boom  # type: ignore[attr-defined]

    res = svc.submit_async(_async_req("a-err"))
    # in-process 실패도 응답 status에 실제 상태(failed)가 반영된다('queued' 거짓표기 제거).
    assert res.status == "failed"
    status = svc.get_status(res.job_id)
    assert status is not None
    assert status.status == "failed"
    assert status.error is not None
    assert "inference broke" in status.error


def test_celery_dispatch_disabled_under_testing_env():
    """Top-7 안전판: TESTING/PYTEST 환경에서는 실 브로커 발사를 절대 시도하지 않는다.

    conftest가 TESTING=1을 세팅하므로 in-process 경로만 타고, 라이브 redis 없이 통과.
    """
    from koipa.services.async_classify_service import _celery_dispatch_available

    assert _celery_dispatch_available() is False


def test_submit_batch_happy_path_3_docs():
    svc = AsyncClassifyService(sleep_fn=lambda _s: None)
    res = svc.submit_batch(_batch_req(3))
    assert res.total == 3
    # 배치도 in-process 전건 처리 후 실제 최종 상태(done)를 반영.
    assert res.status == "done"
    assert res.completed == 3
    assert res.failed == 0
    assert res.failed_doc_ids == []
    assert res.errors == []

    status = svc.get_status(res.job_id)
    assert status is not None
    assert status.status == "done"
    assert status.completed == 3
    assert status.results is not None and len(status.results) == 3


def test_submit_batch_preserves_per_document_client_request_id():
    """[2026-10-13 결함 수정, KL 리포트] 배치 경로는 ClassifyBatchRequest.documents 에
    문서별 client_request_id 를 실을 자리 자체가 없어 결과가 전부 null 이었다.

    문서 순서와 client_request_id 가 1:1로 맞게 돌아오는지 확인 — 순서가 섞이면 호출자가
    요청과 결과를 잘못 짝짓게 되므로 doc_id 뿐 아니라 매칭 자체를 검증한다.
    """
    svc = AsyncClassifyService(sleep_fn=lambda _s: None)
    batch = ClassifyBatchRequest(
        documents=[
            ClassifyRequest(doc_id="b0", content="내용 0", client_request_id="req-0"),
            ClassifyRequest(doc_id="b1", content="내용 1", client_request_id="req-1"),
            ClassifyRequest(doc_id="b2", content="내용 2", client_request_id=None),
        ]
    )
    res = svc.submit_batch(batch)
    assert res.status == "done"

    status = svc.get_status(res.job_id)
    assert status is not None
    assert status.results is not None and len(status.results) == 3
    by_doc_id = {r.doc_id: r.client_request_id for r in status.results}
    assert by_doc_id == {"b0": "req-0", "b1": "req-1", "b2": None}


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


def test_strip_async_fields_keeps_client_request_id():
    """[2026-10-13 정정] 예전엔 callback_url 과 함께 client_request_id 도 떼어내
    classify() 가 결과에 실을 수 없었다 — 이제 callback_url 만 뗀다."""
    req = ClassifyAsyncRequest(doc_id="a-3", content="x", client_request_id="req-keep")
    stripped = AsyncClassifyService._strip_async_fields(req)
    assert stripped.client_request_id == "req-keep"
