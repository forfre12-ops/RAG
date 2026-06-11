"""Async classify service — /classify/async·/batch·/jobs.

PoC: in-process 즉시 실행. 운영은 Celery classify_async.delay(...).
JobStore로 상태 추적.

부분 실패 처리 (2026-05 추가):
- submit_batch는 건별로 독립적인 try/except + retry (지수 백오프 최대 2회).
- 한 건이 영구 실패해도 다음 건 계속 진행.
- 응답에 completed/failed/failed_doc_ids/errors 포함.
- 모든 retry 실패 시 status="partial" (일부 성공) 또는 "failed" (전부 실패).
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Callable, Optional

from lloydk.schemas.classify import ClassifyRequest, ClassifyResponse
from lloydk.schemas.classify_async import (
    ClassifyAsyncRequest,
    ClassifyAsyncResponse,
    ClassifyBatchRequest,
    ClassifyBatchResponse,
    ClassifyJobStatus,
)
from lloydk.services._retry import iter_with_partial_failure
from lloydk.services.classify_service import ClassifyService
from lloydk.services.job_store import get_default_store

logger = logging.getLogger(__name__)


class AsyncClassifyService:
    # 클래스 수준 — 테스트에서 빠른 진행 위해 monkeypatch 가능.
    BATCH_MAX_ATTEMPTS = 3       # 1회 본 시도 + 2회 retry
    BATCH_BASE_DELAY = 0.1       # base_delay; 지수 백오프 0.1/0.2초 (기존 0.5에서 단축)

    def __init__(self, sleep_fn: Callable[[float], None] = time.sleep):
        self.jobs = get_default_store()
        self.classify = ClassifyService.get_instance()
        self._sleep_fn = sleep_fn

    def submit_async(self, req: ClassifyAsyncRequest) -> ClassifyAsyncResponse:
        logger.debug(
            "async classify submit enter: doc_id=%s tenant=%s",
            req.doc_id, getattr(req, "tenant_id", None),
        )
        job_id = uuid.uuid4()
        # 보안(H9): job에 effective tenant 보존 — 이후 GET /classify/jobs/{id}가
        # 호출자 tenant와 일치할 때만 결과 반환(교차테넌트 조회 차단). InMemory·Redis
        # 양쪽 동일하게 payload에 직렬화돼 키 보존.
        self.jobs.create(
            job_id,
            payload={"total": 1, "completed": 0, "tenant_id": req.tenant_id},
        )
        # PoC: 즉시 실행 (Celery 발사 대신)
        callback_payload: dict
        try:
            result = self.classify.classify(self._strip_async_fields(req))
            result_json = result.model_dump(mode="json")
            self.jobs.update(job_id, status="done", completed=1, results=[result_json])
            callback_payload = {
                "job_id": str(job_id),
                "status": "done",
                "results": [result_json],
            }
            logger.info("async classify done: job_id=%s doc_id=%s", job_id, req.doc_id)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "async classify failed: job_id=%s doc_id=%s err=%s",
                job_id, req.doc_id, type(exc).__name__, exc_info=True,
            )
            self.jobs.update(job_id, status="failed", error=str(exc))
            callback_payload = {
                "job_id": str(job_id),
                "status": "failed",
                "error": str(exc),
            }
        # M-callback: callback_url 이 있으면 outbox webhook 발사 — _strip_async_fields
        # 가 callback_url 을 떼어내 분류 자체엔 영향 없게 하면서도, 여기서 결과를
        # outbox 로 publish 해 webhook 이 실제로 울리게 한다(과거엔 영원히 안 울림).
        self._publish_callback(getattr(req, "callback_url", None), callback_payload)
        return ClassifyAsyncResponse(
            job_id=job_id, status="queued", status_url=f"/api/v1/classify/jobs/{job_id}"
        )

    def submit_batch(
        self, req: ClassifyBatchRequest, *, tenant_id: str | None = None
    ) -> ClassifyBatchResponse:
        """건별 isolation + retry 패턴.

        한 건이 영구 실패해도 다음 건 계속 진행. 응답에 부분 실패 정보 포함.
        모두 성공: status="done". 일부 성공: "partial". 전부 실패: "failed".

        보안(H9): ``tenant_id``는 호출자의 effective tenant(엔드포인트가 결속).
        job에 보존되어 GET /classify/jobs/{id}가 호출자 tenant와 일치할 때만
        결과를 반환하게 한다. 명시 인자 미전달 시 배치 내 문서들의 tenant_id가
        모두 동일하면 그 값을, 아니면 None(미스코프 → 폴리시상 일치자만 조회).
        """
        logger.debug("async batch submit enter: total=%d", len(req.documents))
        job_id = uuid.uuid4()
        total = len(req.documents)
        if tenant_id is None:
            doc_tenants = {getattr(d, "tenant_id", None) for d in req.documents}
            tenant_id = doc_tenants.pop() if len(doc_tenants) == 1 else None
        self.jobs.create(
            job_id, payload={"total": total, "completed": 0, "tenant_id": tenant_id}
        )

        def _handle(doc: ClassifyRequest) -> dict:
            res = self.classify.classify(doc)
            return res.model_dump(mode="json")

        # 진행 카운터 업데이트용 wrapper — 매 건 완료 시 jobs.update.
        completed_counter = {"n": 0}

        def _handle_and_track(doc: ClassifyRequest) -> dict:
            out = _handle(doc)
            completed_counter["n"] += 1
            self.jobs.update(job_id, completed=completed_counter["n"])
            return out

        results, failed_ids, errors = iter_with_partial_failure(
            req.documents,
            _handle_and_track,
            max_attempts=self.BATCH_MAX_ATTEMPTS,
            base_delay=self.BATCH_BASE_DELAY,
            sleep_fn=self._sleep_fn,
            id_of=lambda d: d.doc_id,
        )

        completed = len(results)
        failed = len(failed_ids)
        if failed == 0:
            final_status = "done"
        elif completed == 0:
            final_status = "failed"
        else:
            final_status = "partial"

        self.jobs.update(
            job_id,
            status=final_status,
            results=results,
            completed=completed,
            failed=failed,
            failed_doc_ids=failed_ids,
            errors=errors,
        )
        logger.info(
            "async batch done: job_id=%s total=%d completed=%d failed=%d status=%s",
            job_id, total, completed, failed, final_status,
        )

        # M-callback: 배치도 동일하게 callback_url 이 있으면 outbox webhook 발사.
        self._publish_callback(
            getattr(req, "callback_url", None),
            {
                "job_id": str(job_id),
                "status": final_status,
                "total": total,
                "completed": completed,
                "failed": failed,
                "failed_doc_ids": failed_ids,
                "results": results,
            },
        )

        return ClassifyBatchResponse(
            job_id=job_id,
            total=total,
            status="queued",
            status_url=f"/api/v1/classify/jobs/{job_id}",
            completed=completed,
            failed=failed,
            failed_doc_ids=failed_ids,
            errors=errors,
        )

    def get_status(
        self,
        job_id: uuid.UUID,
        *,
        requester_tenant: str | None = None,
        enforce_tenant: bool = False,
    ) -> Optional[ClassifyJobStatus]:
        """job 상태 조회.

        보안(H9): ``enforce_tenant=True``면 job에 보존된 tenant와 호출자
        ``requester_tenant``가 정확히 일치할 때만 반환한다(불일치/미검증이면
        None → 엔드포인트 404). 교차테넌트 job_id 추측으로 타 테넌트 분류
        결과를 열람하지 못하게 한다. enforce_tenant=False는 내부/레거시 호출용.
        """
        logger.debug("async get_status enter: job_id=%s", job_id)
        job = self.jobs.get(job_id)
        if job is None:
            return None
        if enforce_tenant:
            job_tenant = job.get("tenant_id")
            if job_tenant != requester_tenant:
                logger.warning(
                    "job tenant mismatch — job_id=%s job_tenant=%r requester=%r (404)",
                    job_id, job_tenant, requester_tenant,
                )
                return None
        raw_results = job.get("results")
        results = None
        if raw_results:
            results = [ClassifyResponse.model_validate(r) for r in raw_results]
        return ClassifyJobStatus(
            job_id=job_id,
            status=job.get("status", "queued"),
            total=job.get("total"),
            completed=job.get("completed"),
            failed=job.get("failed"),
            failed_doc_ids=job.get("failed_doc_ids", []) or [],
            errors=job.get("errors", []) or [],
            results=results,
            error=job.get("error"),
        )

    @staticmethod
    def _publish_callback(callback_url: str | None, payload: dict) -> None:
        """callback_url 이 있으면 job 결과를 outbox webhook 으로 publish.

        M-callback: callback_url 은 webhook outbox 의 존재 이유인데 그동안
        _strip_async_fields 에서 떨어져 나가고 outbox 로 넘어가지 않아 webhook 이
        절대 울리지 않았다. 여기서 outbox.publish 로 적재 → worker(deliver_once)가
        재시도·DLQ 포함 신뢰성 있게 전달한다.

        best-effort: outbox 적재 실패가 분류 결과 자체를 폐기하면 안 되므로,
        예외는 삼키고 로깅만 한다(분류는 이미 job_store 에 영속화됨).
        """
        if not callback_url:
            return
        try:
            from lloydk.services.outbox import (  # noqa: PLC0415
                get_outbox_store,
                publish,
            )

            publish(get_outbox_store(), target_url=callback_url, payload=payload)
            logger.info("callback enqueued to outbox: url=%s job=%s", callback_url, payload.get("job_id"))
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "callback enqueue failed (non-critical): url=%s err=%s",
                callback_url, type(exc).__name__, exc_info=True,
            )

    @staticmethod
    def _strip_async_fields(req: ClassifyAsyncRequest) -> ClassifyRequest:
        """ClassifyAsyncRequest → ClassifyRequest (callback_url 등 제거)."""
        return ClassifyRequest.model_validate(req.model_dump(exclude={"callback_url"}))
