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
import os
import time
import uuid
from typing import Callable, Optional

from koipa.schemas.classify import ClassifyRequest, ClassifyResponse
from koipa.schemas.classify_async import (
    ClassifyAsyncRequest,
    ClassifyAsyncResponse,
    ClassifyBatchRequest,
    ClassifyBatchResponse,
    ClassifyJobStatus,
)
from koipa.services._retry import iter_with_partial_failure
from koipa.services.classify_service import ClassifyService
from koipa.services.job_store import get_default_store

logger = logging.getLogger(__name__)


def _celery_dispatch_available() -> bool:
    """실 Celery 브로커로 task.delay()를 발사해도 안전한지 런타임 감지.

    Top-7 배선: submit()이 영구 'queued' 거짓표기·in-process 동기실행에 머물지 않고,
    **운영(브로커 가용)** 에서만 실제 비동기 발사하도록 분기하기 위한 게이트.

    True를 반환하는 조건(모두 충족):
      1) 테스트 환경이 아님 (TESTING/PYTEST). 테스트는 라이브 redis 없이 통과해야 하므로
         항상 in-process 경로를 유지한다.
      2) Celery eager 모드가 아님. eager면 .delay()가 in-process 동기실행이라 현재
         경로와 동작이 같으니, JobStore 갱신 의미를 보존하는 in-process 경로를 그대로 쓴다.
      3) 브로커가 실제로 연결 가능(빠른 non-blocking 프로브, max_retries=0).

    하나라도 불충족이면 False → 호출부는 기존 in-process 즉시 실행을 유지(동작·테스트 보존).
    프로브 자체의 예외는 흡수하고 False(보수적). config.py는 건드리지 않고 런타임만 본다.
    """
    if os.getenv("TESTING", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    try:
        import socket  # noqa: PLC0415

        from koipa.workers.celery_app import celery_app  # noqa: PLC0415

        if celery_app.conf.task_always_eager:
            return False
        # 빠른 TCP 프로브(0.5s) — 브로커 미가용 시 kombu 재시도 루프(수초 블로킹) 대신
        # 즉시 False로 떨어져 in-process 폴백한다. conftest._check_postgres와 동일 패턴.
        # 운영(브로커 가용)은 즉시 연결되어 True. 포트 파싱 실패는 보수적으로 False.
        try:
            conn = celery_app.connection_for_write()
            host = getattr(conn, "host", None) or "localhost"
            port = int(getattr(conn, "port", None) or 6379)
            # kombu redis transport은 host에 'host:port' 형태를 담기도 한다 — 포트 분리.
            if ":" in host:
                host = host.rsplit(":", 1)[0]
        except Exception:  # noqa: BLE001
            return False
        try:
            sock = socket.create_connection((host, port), timeout=0.5)
            sock.close()
            return True
        except OSError:
            return False
    except Exception:  # noqa: BLE001
        return False


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
            "async classify submit enter: doc_id=%s",
            req.doc_id,
        )
        job_id = uuid.uuid4()
        self.jobs.create(
            job_id,
            payload={"total": 1, "completed": 0},
        )
        # 운영(브로커 가용): 실제 Celery classify_async.delay() 발사 후 'queued' 반환.
        # job은 'queued'로 남고 worker가 done/failed로 전이 → status가 실제 상태 반영.
        # callback_url은 worker가 결과를 가지므로 여기선 outbox 발사하지 않는다(중복 방지).
        if _celery_dispatch_available():
            try:
                from koipa.workers.tasks import classify_async  # noqa: PLC0415

                payload = self._strip_async_fields(req).model_dump(mode="json")
                # callback_url 을 워커로 넘겨 완료 시 webhook 을 발사하게 한다(과거엔 떼어내
                # 워커에 전달조차 안 돼 운영 경로 webhook 이 영원히 안 울렸다).
                classify_async.delay(
                    payload, job_id=str(job_id), callback_url=getattr(req, "callback_url", None)
                )
                logger.info("async classify enqueued to celery: job_id=%s doc_id=%s", job_id, req.doc_id)
                return ClassifyAsyncResponse(
                    job_id=job_id,
                    status="queued",
                    status_url=f"/api/v1/classify/jobs/{job_id}",
                )
            except Exception:  # noqa: BLE001
                # 발사 실패는 치명적이지 않게 — in-process 폴백으로 진행(가용성 우선).
                logger.warning(
                    "celery enqueue failed for async classify — falling back to in-process: job_id=%s",
                    job_id, exc_info=True,
                )
        # PoC/테스트/브로커 미가용: 즉시 in-process 실행 (Celery 발사 대신)
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
        # in-process 경로는 이미 처리가 끝났으므로 'queued' 거짓표기 대신 실제 최종 상태
        # (done/failed)를 반환한다. status_url 폴링은 동일 값을 다시 보게 된다.
        return ClassifyAsyncResponse(
            job_id=job_id,
            status=callback_payload["status"],
            status_url=f"/api/v1/classify/jobs/{job_id}",
        )

    def submit_batch(
        self, req: ClassifyBatchRequest
    ) -> ClassifyBatchResponse:
        """건별 isolation + retry 패턴.

        한 건이 영구 실패해도 다음 건 계속 진행. 응답에 부분 실패 정보 포함.
        모두 성공: status="done". 일부 성공: "partial". 전부 실패: "failed".
        """
        logger.debug("async batch submit enter: total=%d", len(req.documents))
        job_id = uuid.uuid4()
        total = len(req.documents)
        self.jobs.create(
            job_id, payload={"total": total, "completed": 0}
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

        # 배치는 건별 isolation+retry로 전건을 in-process 처리(결과 인라인)하는 게 계약이라
        # 단발 Celery task로 갈 게 아니다. 처리가 끝났으므로 'queued' 거짓표기 대신 실제
        # 최종 상태(done/partial/failed)를 반환한다.
        return ClassifyBatchResponse(
            job_id=job_id,
            total=total,
            status=final_status,
            status_url=f"/api/v1/classify/jobs/{job_id}",
            completed=completed,
            failed=failed,
            failed_doc_ids=failed_ids,
            errors=errors,
        )

    def get_status(
        self,
        job_id: uuid.UUID,
    ) -> Optional[ClassifyJobStatus]:
        """job 상태 조회.

        tenant 제거: 격리는 KL 포털 전담(단일 고객사 엔진이라 job 격리 불요).
        """
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
    def _publish_callback(callback_url: str | None, payload: dict) -> None:
        """callback_url 로 job 결과 webhook 발사 — outbox.publish_callback(공용 계약)로 위임.

        발사 로직(best-effort·재시도·DLQ)은 outbox 로 통합했다(celery 워커 경로와 중복 제거).
        """
        from koipa.services.outbox import publish_callback  # noqa: PLC0415
        publish_callback(callback_url, payload)

    @staticmethod
    def _strip_async_fields(req: ClassifyAsyncRequest) -> ClassifyRequest:
        """ClassifyAsyncRequest → ClassifyRequest (callback_url 등 제거)."""
        return ClassifyRequest.model_validate(req.model_dump(exclude={"callback_url"}))
