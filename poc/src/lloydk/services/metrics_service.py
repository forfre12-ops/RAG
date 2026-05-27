"""Metrics service — /metrics/latest·/history·/confusion-matrix.

설계 (W4 풀 구현):
- model_versions.metrics JSONB가 있으면 그대로 사용 (학습 후 저장된 stored metrics)
- 없으면 M6 compute_metrics_from_db로 **라이브 평가** (운영 중 분류·보정 누적분 기반)
- confusion-matrix는 M6 build_confusion_matrix로 알람까지 산출
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from lloydk.db import session_scope
from lloydk.db.models import ModelVersion
from lloydk.modules.m6_evaluation import (
    build_confusion_matrix,
    compute_metrics_from_db,
)
from lloydk.modules.m6_evaluation.confusion_matrix import DEFAULT_LABELS
from lloydk.modules.m6_evaluation.metrics import (
    MetricsResult,
    _fetch_truth_pred_pairs,
)
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
                stored = self._from_stored(mv)
                if stored.sample_count and stored.sample_count > 0:
                    return stored
                # stored metrics 없으면 라이브 평가
                live = compute_metrics_from_db(mv.version_label)
                if live is None or live.sample_count == 0:
                    return stored  # 빈 stored 반환 (sample_count=0)
                return self._from_m6(live, mv)
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
                return MetricsHistory(items=[self._from_stored(mv) for mv in rows])
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

                # stored 우선
                stored = mv.metrics or {}
                stored_matrix = stored.get("confusion_matrix")
                stored_labels = stored.get("labels", list(DEFAULT_LABELS))
                if stored_matrix:
                    return ConfusionMatrix(
                        model_version=model_version,
                        labels=stored_labels,
                        matrix=stored_matrix,
                        fnr_by_grade=stored.get("fnr_by_grade", {}),
                    )

                # 라이브 — classifications + corrections에서 페어 추출 후 M6로 CM
                pairs = _fetch_truth_pred_pairs(db, model_version)
                if not pairs:
                    return None
                y_true, y_pred = zip(*pairs)
                cm_result = build_confusion_matrix(list(y_true), list(y_pred))
                # FNR by grade는 metrics 한번 더 계산
                from lloydk.modules.m6_evaluation.metrics import compute_metrics_from_arrays
                m = compute_metrics_from_arrays(list(y_true), list(y_pred), model_version=model_version)
                return ConfusionMatrix(
                    model_version=model_version,
                    labels=cm_result.labels,
                    matrix=cm_result.matrix,
                    fnr_by_grade=m.fnr_by_grade,
                )
        except SQLAlchemyError as exc:
            logger.debug("metrics confusion-matrix skipped: %s", exc)
            return None

    # ------------------------------------------------------------
    # internals
    # ------------------------------------------------------------

    @staticmethod
    def _from_stored(mv: ModelVersion) -> MetricsReport:
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
            sample_count=mv.training_data_count or m.get("sample_count") or 0,
        )

    @staticmethod
    def _from_m6(live: MetricsResult, mv: ModelVersion) -> MetricsReport:
        return MetricsReport(
            model_version=mv.version_label,
            measured_at=mv.trained_at.isoformat() if mv.trained_at else None,
            accuracy=live.accuracy,
            precision_macro=live.precision_macro,
            recall_macro=live.recall_macro,
            f1_macro=live.f1_macro,
            fnr_overall=live.fnr_overall,
            fnr_by_grade=live.fnr_by_grade,
            sample_count=live.sample_count,
        )
