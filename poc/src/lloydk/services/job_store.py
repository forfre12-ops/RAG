"""Thread-safe in-memory job store for async tasks.

PoC 수준 — Celery result backend로 교체 가능. 운영에서는 Redis/PG 권장.

용도:
- /classify/async·/classify/batch 의 job 상태 추적
- /train/jobs/{id} 의 학습 상태 폴링 (DB의 TrainingRun도 진실 소스로 함께 사용)
- /synth/generate 의 생성 작업 추적
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Any

logger = logging.getLogger(__name__)


class JobStore:
    """Thread-safe dict — process-local. 다중 워커 운영 시 Redis로 교체."""

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def create(self, job_id: uuid.UUID, payload: dict[str, Any]) -> None:
        with self._lock:
            self._jobs[str(job_id)] = {"status": "queued", **payload}
        logger.debug("job_store create: job_id=%s keys=%s", job_id, list(payload.keys()))

    def update(self, job_id: uuid.UUID, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(str(job_id))
            if job is None:
                logger.warning("job_store update: job not found — job_id=%s", job_id)
                return
            job.update(fields)
        # status 변동은 운영자 추적 가치 — info 수준
        if "status" in fields:
            logger.info(
                "job_store status: job_id=%s status=%s", job_id, fields.get("status"),
            )
        else:
            logger.debug("job_store update: job_id=%s fields=%s", job_id, list(fields.keys()))

    def get(self, job_id: uuid.UUID) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(str(job_id))
            return dict(job) if job is not None else None

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(j) for j in list(self._jobs.values())[-limit:]]


# module-level singleton
_default = JobStore()


def get_default_store() -> JobStore:
    return _default
