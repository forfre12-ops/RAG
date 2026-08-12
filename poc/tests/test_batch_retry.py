"""Batch 부분 실패 처리·재시도·보상 트랜잭션 검증.

대상: koipa.services.async_classify_service.AsyncClassifyService.submit_batch
- 건별 isolation: 한 건 실패가 다음 건을 막지 않는다.
- retry: 지수 백오프로 최대 2회 재시도 (총 3회 시도).
- 영구 실패 시 failed_doc_ids + errors 기록, 다음 건 계속.
- 응답 status: 모두 성공=done, 일부 성공=partial, 전부 실패=failed.
"""

from __future__ import annotations

import pytest
pytestmark = pytest.mark.slow

from koipa.schemas.classify import ClassifyRequest
from koipa.schemas.classify_async import ClassifyBatchRequest
from koipa.services.async_classify_service import AsyncClassifyService


def _docs(n: int) -> ClassifyBatchRequest:
    return ClassifyBatchRequest(
        documents=[
            ClassifyRequest(doc_id=f"r{i}", content=f"sample content {i}")
            for i in range(n)
        ]
    )


def _make_service_with_flaky_classify(
    fail_plan: dict[str, int],  # doc_id -> 실패시킬 연속 횟수 (이후 성공)
    permanent_fail_ids: set[str] | None = None,
) -> tuple[AsyncClassifyService, dict[str, int]]:
    """ClassifyService.classify를 가짜 동작으로 교체한 service 인스턴스 반환.

    fail_plan: doc_id마다 처음 N회는 RuntimeError, 그 뒤는 성공.
    permanent_fail_ids: 끝까지 영구 실패.
    """
    svc = AsyncClassifyService(sleep_fn=lambda _s: None)  # sleep no-op
    permanent = permanent_fail_ids or set()
    call_counts: dict[str, int] = {}

    real_classify = svc.classify.classify  # type: ignore[attr-defined]

    def _fake_classify(req: ClassifyRequest):
        call_counts[req.doc_id] = call_counts.get(req.doc_id, 0) + 1
        if req.doc_id in permanent:
            raise RuntimeError(f"permanent failure for {req.doc_id}")
        budget = fail_plan.get(req.doc_id, 0)
        if call_counts[req.doc_id] <= budget:
            raise RuntimeError(f"transient failure {call_counts[req.doc_id]}/{budget} for {req.doc_id}")
        return real_classify(req)

    svc.classify.classify = _fake_classify  # type: ignore[attr-defined]
    return svc, call_counts


@pytest.fixture(autouse=True)
def _reset_singleton():
    yield
    from koipa.services.classify_service import ClassifyService
    ClassifyService._instance = None


# ----------------------------------------------------------------------
# 정상 경로
# ----------------------------------------------------------------------

def test_batch_all_succeed_5_docs():
    """N=5 모두 성공 → completed=5, failed=0, status=done."""
    svc = AsyncClassifyService(sleep_fn=lambda _s: None)
    req = _docs(5)
    res = svc.submit_batch(req)
    assert res.total == 5
    assert res.completed == 5
    assert res.failed == 0
    assert res.failed_doc_ids == []
    assert res.errors == []


# ----------------------------------------------------------------------
# 일시 실패 → retry 성공
# ----------------------------------------------------------------------

def test_batch_transient_failure_recovers_via_retry():
    """3번째 건 1회 일시 실패 후 retry로 성공 → 모두 성공."""
    svc, counts = _make_service_with_flaky_classify(fail_plan={"r2": 1})
    req = _docs(5)
    res = svc.submit_batch(req)
    assert res.completed == 5
    assert res.failed == 0
    assert res.failed_doc_ids == []
    # r2는 retry로 2회 호출되어야 한다 (첫 실패 + 두번째 성공).
    assert counts["r2"] == 2


def test_batch_transient_failure_two_retries_recovers():
    """3번째 건 2회 연속 일시 실패 후 마지막 attempt에서 성공."""
    svc, counts = _make_service_with_flaky_classify(fail_plan={"r2": 2})
    req = _docs(5)
    res = svc.submit_batch(req)
    assert res.completed == 5
    assert res.failed == 0
    # max_attempts=3 → r2는 정확히 3회 호출되어야 함.
    assert counts["r2"] == 3


# ----------------------------------------------------------------------
# 영구 실패 — 나머지 건은 진행
# ----------------------------------------------------------------------

def test_batch_permanent_failure_continues_other_docs():
    """3번째(r2)가 영구 실패해도 r0,r1,r3,r4는 진행 + failed_doc_ids에 r2 기록."""
    svc, counts = _make_service_with_flaky_classify(
        fail_plan={}, permanent_fail_ids={"r2"},
    )
    req = _docs(5)
    res = svc.submit_batch(req)
    assert res.total == 5
    assert res.completed == 4
    assert res.failed == 1
    assert res.failed_doc_ids == ["r2"]
    assert len(res.errors) == 1
    err = res.errors[0]
    assert err["doc_id"] == "r2"
    assert err["error_type"] == "RuntimeError"
    assert err["attempts"] == 3  # max_attempts=3 모두 소진
    # r3, r4 도 호출되어야 한다 → 건별 isolation 검증.
    assert counts["r2"] == 3
    assert counts.get("r3", 0) == 1
    assert counts.get("r4", 0) == 1


def test_batch_partial_status_when_some_fail():
    """일부 영구 실패 → status=partial (JobStore 측 상태)."""
    svc, _ = _make_service_with_flaky_classify(
        fail_plan={}, permanent_fail_ids={"r1", "r3"},
    )
    req = _docs(5)
    res = svc.submit_batch(req)
    status = svc.get_status(res.job_id)
    assert status is not None
    assert status.status == "partial"
    assert status.completed == 3
    assert status.failed == 2
    assert set(status.failed_doc_ids) == {"r1", "r3"}


# ----------------------------------------------------------------------
# 모든 건 실패
# ----------------------------------------------------------------------

def test_batch_all_fail_status_failed():
    """모든 건 영구 실패 → status=failed."""
    svc, _ = _make_service_with_flaky_classify(
        fail_plan={}, permanent_fail_ids={f"r{i}" for i in range(3)},
    )
    req = _docs(3)
    res = svc.submit_batch(req)
    assert res.completed == 0
    assert res.failed == 3
    assert set(res.failed_doc_ids) == {"r0", "r1", "r2"}
    status = svc.get_status(res.job_id)
    assert status is not None
    assert status.status == "failed"


# ----------------------------------------------------------------------
# 회귀: boundary 기존 테스트가 깨지지 않음
# ----------------------------------------------------------------------

def test_regression_empty_batch_boundary_test_unaffected():
    """기존 test_batch_boundary.test_batch_empty_rejected_400는 API 레벨에서
    /classify/batch가 빈 배열을 400으로 막는다. 서비스 자체는 빈 배열을 받으면
    total=0·completed=0·failed=0의 '아무것도 하지 않은' 응답을 안정적으로 반환해야 한다.
    """
    svc = AsyncClassifyService(sleep_fn=lambda _s: None)
    req = ClassifyBatchRequest(documents=[])
    res = svc.submit_batch(req)
    assert res.total == 0
    assert res.completed == 0
    assert res.failed == 0
    assert res.failed_doc_ids == []
    assert res.errors == []
