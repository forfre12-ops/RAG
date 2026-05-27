"""Training service — TrainingRun + Celery 트리거 + 상태 폴링.

PoC 흐름:
1. /train POST → TrainingRun(status='queued') + JobStore에 등록
2. classify_async와 달리 실 학습은 무거우니, 본 PoC는 즉시 'queued'만 반환
   (운영에서는 Celery train_classifier_task 발사)
3. /train/jobs/{id} GET → TrainingRun(DB) + JobStore(메모리) 머지
4. /train/jobs GET → TrainingRun 최근 목록
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Optional

from sqlalchemy.exc import SQLAlchemyError

from lloydk.db import session_scope
from lloydk.repositories import TrainingRepo
from lloydk.schemas.training import (
    TrainJobList,
    TrainJobSummary,
    TrainRequest,
    TrainResponse,
    TrainStatus,
)
from lloydk.services.job_store import get_default_store

logger = logging.getLogger(__name__)


class TrainingService:
    def __init__(self):
        self.jobs = get_default_store()

    def submit(self, req: TrainRequest) -> TrainResponse:
        """학습 작업 등록. DB에 TrainingRun(queued) 1건 생성."""
        run_id = self._create_run(req)
        # JobStore 등록 (DB 미가용 시에도 응답 가능)
        self.jobs.create(
            run_id,
            payload={
                "training_type": req.training_type,
                "base_model": req.base_model,
                "submitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "actor": req.actor.user_id,
            },
        )
        # NOTE: 운영은 여기서 Celery train_classifier_task.delay(...) 호출.
        # PoC는 GPU/데이터셋 미확보라 'queued' 유지.
        return TrainResponse(
            train_job_id=run_id,
            status_url=f"/api/v1/train/jobs/{run_id}",
            websocket_url=None,
        )

    def status(self, train_job_id: uuid.UUID) -> Optional[TrainStatus]:
        # DB(TrainingRun) 우선, 없으면 JobStore
        run = self._get_run(train_job_id)
        if run is not None:
            return TrainStatus(
                train_job_id=train_job_id,
                status=run.status,
                progress=self._progress_from_status(run.status),
                started_at=run.started_at.isoformat() if run.started_at else None,
                error=run.error_message,
            )
        job = self.jobs.get(train_job_id)
        if job is None:
            return None
        return TrainStatus(
            train_job_id=train_job_id,
            status=job.get("status", "queued"),
            progress=0.0,
        )

    def list_recent(self, limit: int = 20, status_filter: Optional[str] = None) -> TrainJobList:
        try:
            with session_scope() as db:
                repo = TrainingRepo(db)
                runs = repo.list_recent_runs(limit=limit)
                if status_filter:
                    runs = [r for r in runs if r.status == status_filter]
                items = [
                    TrainJobSummary(
                        train_job_id=r.run_id,
                        status=r.status,
                        started_at=r.started_at.isoformat() if r.started_at else None,
                        completed_at=r.completed_at.isoformat() if r.completed_at else None,
                        duration_sec=r.duration_sec,
                        model_version=None,
                        trigger_type=r.trigger_type,
                    )
                    for r in runs
                ]
                return TrainJobList(total=len(items), items=items)
        except SQLAlchemyError as exc:
            logger.debug("train list skipped: %s", exc)
            return TrainJobList(total=0, items=[])

    # ------------------------------------------------------------
    # internals
    # ------------------------------------------------------------

    def _create_run(self, req: TrainRequest) -> uuid.UUID:
        try:
            with session_scope() as db:
                repo = TrainingRepo(db)
                run = repo.create_run(
                    total_samples=0,  # PoC: 데이터셋 미확정
                    hyperparameters=req.hyperparams,
                    trigger_type=req.training_type,
                    created_by=req.actor.user_id,
                )
                return run.run_id
        except SQLAlchemyError as exc:
            logger.debug("training run create skipped (DB unavailable): %s", exc)
            return uuid.uuid4()

    def _get_run(self, run_id: uuid.UUID):
        try:
            with session_scope() as db:
                return TrainingRepo(db).get_run(run_id)
        except SQLAlchemyError:
            return None

    @staticmethod
    def _progress_from_status(status: str) -> float:
        return {
            "queued": 0.0,
            "running": 0.5,
            "completed": 1.0,
            "failed": 0.0,
        }.get(status, 0.0)
