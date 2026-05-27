"""Metrics service — /metrics/latest·/history·/confusion-matrix.

PoC 스텁: M6 평가 모듈(W4)이 본격적으로 채울 곳. 본 서비스는
- 현재 운영 모델(model_versions.is_active=TRUE) 메트릭을 직접 반환
- M6가 활성화되면 metrics 컬럼 + per_class 추가됨
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from lloydk.db import session_scope
from lloydk.db.models import ModelVersion
from lloydk.schemas.metrics import ConfusionMatrix, MetricsHistory, MetricsReport

logger = logging.getLogger(__name__)


class MetricsService:
    def latest(self) -> Optional[MetricsReport]:
        try:
            with session_scope() as db:
                mv = db.execute(
                    select(ModelVersion).where(ModelVersion.is_active.is_(True))
                ).scalar_one_or_none()
                if mv is None:
                    return None
                return self._to_report(mv)
        except SQLAlchemyError as exc:
            logger.debug("metrics latest skipped: %s", exc)
            return None

    def history(self, limit: int = 20) -> MetricsHistory:
        try:
            with session_scope() as db:
                rows = list(
                    db.execute(
                        select(ModelVersion).order_by(ModelVersion.created_at.desc()).limit(limit)
                    ).scalars()
                )
                return MetricsHistory(items=[self._to_report(mv) for mv in rows])
        except SQLAlchemyError as exc:
            logger.debug("metrics history skipped: %s", exc)
            return MetricsHistory(items=[])

    def confusion_matrix(self, model_version: str) -> Optional[ConfusionMatrix]:
        try:
            with session_scope() as db:
                mv = db.execute(
                    select(ModelVersion).where(ModelVersion.version_label == model_version)
                ).scalar_one_or_none()
                if mv is None:
                    return None
                m = mv.metrics or {}
                matrix = m.get("confusion_matrix", [])
                fnr = m.get("fnr_by_grade", {})
                labels = m.get("labels", ["TS", "S1", "S2", "S3"])
                return ConfusionMatrix(
                    model_version=model_version,
                    labels=labels,
                    matrix=matrix,
                    fnr_by_grade=fnr,
                )
        except SQLAlchemyError as exc:
            logger.debug("metrics confusion-matrix skipped: %s", exc)
            return None

    @staticmethod
    def _to_report(mv: ModelVersion) -> MetricsReport:
        m = mv.metrics or {}
        return MetricsReport(
            model_version=mv.version_label,
            measured_at=mv.trained_at.isoformat() if mv.trained_at else None,
            accuracy=m.get("accuracy"),
            precision_macro=m.get("precision_macro"),
            recall_macro=m.get("recall_macro"),
            f1_macro=m.get("f1_macro"),
            fnr_overall=m.get("fnr_overall"),
            fnr_by_grade=m.get("fnr_by_grade", {}),
            sample_count=mv.training_data_count,
        )
