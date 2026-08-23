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

from koipa.db import session_scope
from koipa.db.models import ModelVersion
from koipa.modules.m6_evaluation import (
    build_confusion_matrix,
    compute_metrics_from_db,
)
from koipa.modules.m6_evaluation.confusion_matrix import DEFAULT_LABELS
from koipa.modules.m6_evaluation.metrics import (
    MetricsResult,
    _fetch_truth_pred_pairs,
)
from koipa.schemas.metrics import ConfusionMatrix, MetricsHistory, MetricsReport

logger = logging.getLogger(__name__)


class MetricsService:
    def latest(self) -> Optional[MetricsReport]:
        logger.debug("metrics latest enter")
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
                logger.info(
                    "metrics latest done (live): model_version=%s sample_count=%d",
                    mv.version_label, live.sample_count,
                )
                return self._from_m6(live, mv)
        except SQLAlchemyError as exc:
            logger.warning("metrics latest skipped: %s", exc)
            return None

    def history(self, limit: int = 20) -> MetricsHistory:
        logger.debug("metrics history enter: limit=%d", limit)
        try:
            with session_scope() as db:
                rows = list(
                    db.execute(
                        select(ModelVersion).order_by(ModelVersion.created_at.desc()).limit(limit)
                    ).scalars()
                )
                return MetricsHistory(items=[self._from_stored(mv) for mv in rows])
        except SQLAlchemyError as exc:
            logger.warning("metrics history skipped: %s", exc)
            return MetricsHistory(items=[])

    def confusion_matrix(self, model_version: str) -> Optional[ConfusionMatrix]:
        logger.debug("metrics confusion_matrix enter: model_version=%s", model_version)
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
                from koipa.modules.m6_evaluation.metrics import compute_metrics_from_arrays
                m = compute_metrics_from_arrays(list(y_true), list(y_pred), model_version=model_version)
                logger.info(
                    "metrics confusion_matrix done: model_version=%s pairs=%d",
                    model_version, len(pairs),
                )
                return ConfusionMatrix(
                    model_version=model_version,
                    labels=cm_result.labels,
                    matrix=cm_result.matrix,
                    fnr_by_grade=m.fnr_by_grade,
                )
        except SQLAlchemyError as exc:
            logger.warning("metrics confusion-matrix skipped: %s", exc)
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
            sample_count=MetricsService._eval_sample_count(mv, m),
            eval_source=m.get("eval_source"),
            fnr_high=m.get("fnr_high"),
        )

    @staticmethod
    def _eval_sample_count(mv: ModelVersion, m: dict) -> int:
        """MetricsReport.sample_count = **평가 표본 수**.

        [실측 2026-08-08] 종전에는 mv.training_data_count 를 먼저 봤다. 그 컬럼은 학습 카운트라
        의미가 다르고(같은 필드를 _from_m6 는 live.sample_count = 평가 표본 수로 채운다 —
        경로에 따라 뜻이 갈렸다), 게다가 학습 경로가 그 자리에 '병합된 교정 건수'를 넣고 있었다.
        그 결과 2,044행으로 학습해 256건으로 평가한 모델이 화면에 sample_count=2 로 표시됐다.
        학습 경로가 저장하는 metrics 는 TrainReport 전체라 sample_count 키가 없고 confusion_matrix
        만 있으므로, 없으면 혼동행렬 셀 합으로 유도한다(register_deployed_model 과 동일 규칙).
        training_data_count 는 둘 다 없을 때의 마지막 폴백으로만 남긴다(하위호환).
        """
        n = m.get("sample_count")
        if n:
            return int(n)
        cm = m.get("confusion_matrix") or []
        if cm:
            try:
                return int(sum(sum(row) for row in cm))
            except Exception:  # noqa: BLE001
                pass
        return int(mv.training_data_count or 0)

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
