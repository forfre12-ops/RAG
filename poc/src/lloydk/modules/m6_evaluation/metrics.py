"""평가 지표 계산 — Accuracy·Precision·Recall·F1·FNR per-grade.

설계:
- compute_metrics_from_arrays(y_true, y_pred, labels) — 순수 함수 (테스트 편의)
- compute_metrics_from_db(model_version) — DB의 classifications + corrections에서
  진실 라벨 추출 (corrections 최신 > predicted) 후 위 함수 호출

FNR per-grade는 본 사업 핵심 KPI. 보안 미탐(undeclass) = 고등급 정답을 저등급으로
예측 = row 합에서 대각선 빼고 = 미탐 건. 등급별 분리 보고.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    precision_recall_fscore_support,
)
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from lloydk.db import session_scope
from lloydk.db.models import Classification, ClassificationLevel, Correction

DEFAULT_LABELS = ["TS", "S1", "S2", "S3"]


@dataclass
class MetricsResult:
    model_version: str
    labels: list[str]
    sample_count: int
    accuracy: float
    precision_macro: float
    recall_macro: float
    f1_macro: float
    fnr_overall: float
    fnr_by_grade: dict[str, float] = field(default_factory=dict)
    per_class: dict[str, dict[str, float]] = field(default_factory=dict)
    confusion_matrix: list[list[int]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "model_version": self.model_version,
            "labels": self.labels,
            "sample_count": self.sample_count,
            "accuracy": self.accuracy,
            "precision_macro": self.precision_macro,
            "recall_macro": self.recall_macro,
            "f1_macro": self.f1_macro,
            "fnr_overall": self.fnr_overall,
            "fnr_by_grade": self.fnr_by_grade,
            "per_class": self.per_class,
            "confusion_matrix": self.confusion_matrix,
        }


def compute_metrics_from_arrays(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    *,
    labels: Sequence[str] | None = None,
    model_version: str = "n/a",
) -> MetricsResult:
    """y_true·y_pred는 등급 코드 문자열 (TS·S1·S2·S3) 시퀀스."""
    label_list = list(labels) if labels else list(DEFAULT_LABELS)
    n = len(y_true)
    if n == 0 or n != len(y_pred):
        return _empty_result(model_version, label_list)

    cm = confusion_matrix(y_true, y_pred, labels=label_list)
    acc = float(accuracy_score(y_true, y_pred))
    p, r, f, _ = precision_recall_fscore_support(
        y_true, y_pred, labels=label_list, average="macro", zero_division=0
    )
    p_per, r_per, f_per, support_per = precision_recall_fscore_support(
        y_true, y_pred, labels=label_list, average=None, zero_division=0
    )

    fnr_overall, fnr_by = _fnr_from_cm(cm, label_list)
    per_class = {
        lbl: {
            "precision": float(p_per[i]),
            "recall": float(r_per[i]),
            "f1": float(f_per[i]),
            "support": int(support_per[i]),
            "fnr": float(fnr_by.get(lbl, 0.0)),
        }
        for i, lbl in enumerate(label_list)
    }

    return MetricsResult(
        model_version=model_version,
        labels=label_list,
        sample_count=n,
        accuracy=acc,
        precision_macro=float(p),
        recall_macro=float(r),
        f1_macro=float(f),
        fnr_overall=fnr_overall,
        fnr_by_grade=fnr_by,
        per_class=per_class,
        confusion_matrix=cm.tolist(),
    )


def compute_metrics_from_db(
    model_version: str,
    *,
    tenant_id: str | None = None,
    labels: Sequence[str] | None = None,
) -> MetricsResult | None:
    """DB의 classifications + corrections로 진실 라벨 vs 예측 라벨 페어 추출.

    진실 라벨:
      - corrections.direction != 'confirm' 인 가장 최신 corrected_level → 정답
      - 보정 없으면 → predicted_level이 정답 (확정 가정)

    None 반환: DB 미가용 / 해당 model_version 분류 0건.
    """
    try:
        with session_scope() as db:
            pairs = _fetch_truth_pred_pairs(db, model_version, tenant_id=tenant_id)
    except SQLAlchemyError:
        return None

    if not pairs:
        return None

    y_true, y_pred = zip(*pairs)
    return compute_metrics_from_arrays(
        list(y_true), list(y_pred), labels=labels, model_version=model_version
    )


# ------------------------------------------------------------
# internals
# ------------------------------------------------------------


def _fnr_from_cm(cm: np.ndarray, labels: Sequence[str]) -> tuple[float, dict[str, float]]:
    """행=정답, 열=예측. FNR_i = (row_sum - tp) / row_sum."""
    fnr_by: dict[str, float] = {}
    fn_total = 0
    pos_total = 0
    for i, name in enumerate(labels):
        row_sum = int(cm[i].sum())
        tp = int(cm[i, i])
        fn = row_sum - tp
        fnr_by[name] = float(fn / row_sum) if row_sum else 0.0
        fn_total += fn
        pos_total += row_sum
    overall = float(fn_total / pos_total) if pos_total else 0.0
    return overall, fnr_by


def _fetch_truth_pred_pairs(
    db: Session,
    model_version: str,
    *,
    tenant_id: str | None = None,
) -> list[tuple[str, str]]:
    # level_id → code 매핑
    code_by_id = {
        lv.level_id: lv.level_code
        for lv in db.execute(select(ClassificationLevel)).scalars()
    }

    # classifications 조회
    stmt = select(Classification).where(Classification.model_version == model_version)
    if tenant_id:
        stmt = stmt.where(Classification.tenant_id == tenant_id)
    cls_list = list(db.execute(stmt).scalars())
    if not cls_list:
        return []

    # 각 classification의 최신 corrections (direction!=confirm) 1건 lookup
    cls_ids = [c.classification_id for c in cls_list]
    corr_rows = list(
        db.execute(
            select(Correction)
            .where(Correction.classification_id.in_(cls_ids))
            .where(Correction.direction != "confirm")
            .order_by(Correction.corrected_at.desc())
        ).scalars()
    )
    latest_corr: dict = {}
    for c in corr_rows:
        latest_corr.setdefault(c.classification_id, c.corrected_level_id)

    pairs: list[tuple[str, str]] = []
    for c in cls_list:
        pred_code = code_by_id.get(c.predicted_level_id)
        if pred_code is None:
            continue
        truth_level_id = latest_corr.get(c.classification_id, c.predicted_level_id)
        truth_code = code_by_id.get(truth_level_id, pred_code)
        pairs.append((truth_code, pred_code))
    return pairs


def _empty_result(model_version: str, labels: list[str]) -> MetricsResult:
    return MetricsResult(
        model_version=model_version,
        labels=labels,
        sample_count=0,
        accuracy=0.0,
        precision_macro=0.0,
        recall_macro=0.0,
        f1_macro=0.0,
        fnr_overall=0.0,
        fnr_by_grade={lbl: 0.0 for lbl in labels},
        per_class={},
        confusion_matrix=[],
    )
