"""Async classify service — /classify/async·/batch·/jobs.

PoC: in-process 즉시 실행. 운영은 Celery classify_async.delay(...).
JobStore로 상태 추적.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from lloydk.schemas.classify import ClassifyRequest, ClassifyResponse
from lloydk.schemas.classify_async import (
    ClassifyAsyncRequest,
    ClassifyAsyncResponse,
    ClassifyBatchRequest,
    ClassifyBatchResponse,
    ClassifyJobStatus,
)
from lloydk.services.classify_service import ClassifyService
from lloydk.services.job_store import get_default_store

logger = logging.getLogger(__name__)


class AsyncClassifyService:
    def __init__(self):
        self.jobs = get_default_store()
        self.classify = ClassifyService.get_instance()

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
        logger.debug("async batch submit enter: total=%d", len(req.documents))
        job_id = uuid.uuid4()
        total = len(req.documents)
        self.jobs.create(job_id, payload={"total": total, "completed": 0})
        results: list[dict] = []
        try:
            for doc in req.documents:
                res = self.classify.classify(doc)
                results.append(res.model_dump(mode="json"))
                self.jobs.update(job_id, completed=len(results))
            self.jobs.update(job_id, status="done", results=results)
            logger.info("async batch done: job_id=%s total=%d", job_id, total)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "async batch failed: job_id=%s completed=%d total=%d err=%s",
                job_id, len(results), total, type(exc).__name__, exc_info=True,
            )
            self.jobs.update(job_id, status="failed", error=str(exc), results=results)
        return ClassifyBatchResponse(
            job_id=job_id,
            total=total,
            status="queued",
            status_url=f"/api/v1/classify/jobs/{job_id}",
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
            results=results,
            error=job.get("error"),
        )

    @staticmethod
    def _strip_async_fields(req: ClassifyAsyncRequest) -> ClassifyRequest:
        """ClassifyAsyncRequest → ClassifyRequest (callback_url 등 제거)."""
        return ClassifyRequest.model_validate(req.model_dump(exclude={"callback_url"}))
