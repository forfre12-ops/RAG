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
    BATCH_MAX_ATTEMPTS = 3       # 1회 본 시도 + 2회 retry (요구사항)
    BATCH_BASE_DELAY = 0.5       # base_delay; 지수 백오프 0.5/1.0초

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
        self.jobs.create(job_id, payload={"total": 1, "completed": 0})
        # PoC: 즉시 실행 (Celery 발사 대신)
        try:
            result = self.classify.classify(self._strip_async_fields(req))
            self.jobs.update(job_id, status="done", completed=1, results=[result.model_dump(mode="json")])
            logger.info("async classify done: job_id=%s doc_id=%s", job_id, req.doc_id)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "async classify failed: job_id=%s doc_id=%s err=%s",
                job_id, req.doc_id, type(exc).__name__, exc_info=True,
            )
            self.jobs.update(job_id, status="failed", error=str(exc))
        return ClassifyAsyncResponse(
            job_id=job_id, status="queued", status_url=f"/api/v1/classify/jobs/{job_id}"
        )

    def submit_batch(self, req: ClassifyBatchRequest) -> ClassifyBatchResponse:
        """건별 isolation + retry 패턴.

        한 건이 영구 실패해도 다음 건 계속 진행. 응답에 부분 실패 정보 포함.
        모두 성공: status="done". 일부 성공: "partial". 전부 실패: "failed".
        """
        logger.debug("async batch submit enter: total=%d", len(req.documents))
        job_id = uuid.uuid4()
        total = len(req.documents)
        self.jobs.create(job_id, payload={"total": total, "completed": 0})

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

    def get_status(self, job_id: uuid.UUID) -> Optional[ClassifyJobStatus]:
        logger.debug("async get_status enter: job_id=%s", job_id)
        job = self.jobs.get(job_id)
        if job is None:
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
    def _strip_async_fields(req: ClassifyAsyncRequest) -> ClassifyRequest:
        """ClassifyAsyncRequest → ClassifyRequest (callback_url 등 제거)."""
        return ClassifyRequest.model_validate(req.model_dump(exclude={"callback_url"}))
