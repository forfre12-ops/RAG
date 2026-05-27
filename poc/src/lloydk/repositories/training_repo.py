"""Training/Model 도메인 — TrainingRun · TrainingEpoch · TrainingDataset · ModelVersion."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from lloydk.db.models import (
    ModelVersion,
    TrainingDataset,
    TrainingEpoch,
    TrainingRun,
)


class TrainingRepo:
    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------
    # TrainingRun
    # ------------------------------------------------------------

    def create_run(
        self,
        *,
        total_samples: int,
        train_count: int | None = None,
        val_count: int | None = None,
        test_count: int | None = None,
        hyperparameters: dict | None = None,
        trigger_type: str = "manual",
        trigger_ref: str | None = None,
        created_by: str | None = None,
    ) -> TrainingRun:
        run = TrainingRun(
            total_samples=total_samples,
            train_count=train_count,
            val_count=val_count,
            test_count=test_count,
            hyperparameters=hyperparameters or {},
            trigger_type=trigger_type,
            trigger_ref=trigger_ref,
            created_by=created_by,
        )
        self.db.add(run)
        self.db.flush()
        return run

    def mark_started(self, run_id: uuid.UUID) -> None:
        run = self.db.get(TrainingRun, run_id)
        if run is None:
            return
        run.status = "running"
        run.started_at = dt.datetime.now(dt.timezone.utc)

    def mark_completed(
        self,
        run_id: uuid.UUID,
        *,
        final_metrics: dict,
        duration_sec: int | None = None,
    ) -> None:
        run = self.db.get(TrainingRun, run_id)
        if run is None:
            return
        run.status = "completed"
        run.completed_at = dt.datetime.now(dt.timezone.utc)
        run.final_metrics = final_metrics
        run.duration_sec = duration_sec

    def mark_failed(self, run_id: uuid.UUID, error_message: str) -> None:
        run = self.db.get(TrainingRun, run_id)
        if run is None:
            return
        run.status = "failed"
        run.completed_at = dt.datetime.now(dt.timezone.utc)
        run.error_message = error_message

    def get_run(self, run_id: uuid.UUID) -> TrainingRun | None:
        return self.db.get(TrainingRun, run_id)

    def list_recent_runs(self, *, limit: int = 20) -> list[TrainingRun]:
        return list(
            self.db.execute(
                select(TrainingRun)
                .order_by(TrainingRun.created_at.desc())
                .limit(limit)
            ).scalars()
        )

    # ------------------------------------------------------------
    # TrainingEpoch
    # ------------------------------------------------------------

    def log_epoch(
        self,
        run_id: uuid.UUID,
        epoch: int,
        *,
        train_loss: float | None = None,
        val_loss: float | None = None,
        val_metrics: dict | None = None,
        learning_rate: float | None = None,
    ) -> TrainingEpoch:
        ep = TrainingEpoch(
            run_id=run_id,
            epoch=epoch,
            train_loss=train_loss,
            val_loss=val_loss,
            val_metrics=val_metrics or {},
            learning_rate=learning_rate,
        )
        self.db.add(ep)
        return ep

    def epochs_for_run(self, run_id: uuid.UUID) -> list[TrainingEpoch]:
        return list(
            self.db.execute(
                select(TrainingEpoch)
                .where(TrainingEpoch.run_id == run_id)
                .order_by(TrainingEpoch.epoch)
            ).scalars()
        )

    # ------------------------------------------------------------
    # TrainingDataset
    # ------------------------------------------------------------

    def register_dataset_rows(
        self,
        run_id: uuid.UUID,
        rows: Iterable[tuple[uuid.UUID, str, int]],
    ) -> int:
        """rows: iterable of (doc_id, split_type, level_id)."""
        count = 0
        for doc_id, split_type, level_id in rows:
            self.db.add(
                TrainingDataset(
                    run_id=run_id,
                    doc_id=doc_id,
                    split_type=split_type,
                    level_id=level_id,
                )
            )
            count += 1
        return count

    # ------------------------------------------------------------
    # ModelVersion
    # ------------------------------------------------------------

    def register_model_version(
        self,
        *,
        version_label: str,
        base_model: str,
        metrics: dict,
        training_run_id: uuid.UUID | None = None,
        mlflow_run_id: str | None = None,
        model_uri: str | None = None,
        level_snapshot: dict | None = None,
    ) -> ModelVersion:
        mv = ModelVersion(
            version_label=version_label,
            base_model=base_model,
            metrics=metrics,
            training_run_id=training_run_id,
            mlflow_run_id=mlflow_run_id,
            model_uri=model_uri,
            level_snapshot=level_snapshot,
            trained_at=dt.datetime.now(dt.timezone.utc),
        )
        self.db.add(mv)
        self.db.flush()
        return mv

    def activate_model_version(self, version_id: uuid.UUID) -> None:
        """이전 활성 모델을 비활성화하고, 지정된 버전을 활성화."""
        now = dt.datetime.now(dt.timezone.utc)
        # is_active=TRUE 부분 인덱스 위반 방지: 먼저 모든 활성 모델 비활성화
        active_rows = (
            self.db.execute(select(ModelVersion).where(ModelVersion.is_active.is_(True)))
            .scalars()
            .all()
        )
        for mv in active_rows:
            mv.is_active = False
            mv.deactivated_at = now
        self.db.flush()  # UNIQUE WHERE is_active=TRUE 인덱스 충돌 방지
        target = self.db.get(ModelVersion, version_id)
        if target is not None:
            target.is_active = True
            target.activated_at = now

    def get_active(self) -> ModelVersion | None:
        return self.db.execute(
            select(ModelVersion).where(ModelVersion.is_active.is_(True))
        ).scalar_one_or_none()

    def get_by_label(self, version_label: str) -> ModelVersion | None:
        return self.db.execute(
            select(ModelVersion).where(ModelVersion.version_label == version_label)
        ).scalar_one_or_none()
